"""Coordinator answer publication, typed validation, and safe fallback (R5)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from app.business_query.outcomes import BusinessQueryWireOutcome, UnifiedResultEnvelope
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.conversation.coordinator.action_lifecycle import ActionOutcome
from app.conversation.coordinator.contracts import FinishAnswer, Observation, ValueRef
from app.conversation.followup_context import SelectedSources
from app.core.turn_budget import TurnBudget
from app.models.citations import CitationsPayload, CitedSourceRef
from app.models.tool_results import TurnResult
from app.providers.model_purpose import ModelPurpose
from app.providers.stage_model_report import StageModelAccumulator
from app.rag.retrieval.document_contracts import DocumentSearchResult
from app.rag.retrieval.passages import RetrievedSource
from app.services.ask_outcome import Answered, FixedMessage

NARRATIVE_UNAVAILABLE_MESSAGE = (
    "I could not write the explanation for these results. "
    "The tables above are complete and current."
)

_SQL_RE = re.compile(r"(?i)\bselect\s+\*|\bselect\b.*?\bfrom\b|\bbilling_[a-zA-Z0-9_]+\b")

# The longest id a cited reference takes (CitedSourceRef.id).
_MAX_CITED_ID_CHARS = 256


def committed_business_result(outcomes: Sequence[ActionOutcome]) -> AskBusinessQueryResult | None:
    """The last business result the turn holds, with its wire outcome — answered or not.

    A finished answer written over it carries it forward: the evidence snapshot,
    the restore reference and the tables when it answered; the reason code and
    the trace when it did not.
    """
    found: AskBusinessQueryResult | None = None
    for outcome in outcomes:
        res = outcome.result
        if isinstance(res, CommittedBqResult):
            res = res.result
        if isinstance(res, AskBusinessQueryResult) and res.business_query is not None:
            found = res
    return found


def retrieved_passages(outcomes: Sequence[ActionOutcome]) -> tuple[RetrievedSource, ...]:
    """Every distinct passage the turn's document searches returned, in search order.

    A passage that two searches return counts once.
    """
    distinct: dict[str, RetrievedSource] = {}
    for outcome in outcomes:
        if isinstance(outcome.result, DocumentSearchResult):
            for passage in outcome.result.passages:
                distinct.setdefault(passage.id, passage)
    return tuple(distinct.values())


@dataclass(frozen=True)
class CitedPassages:
    """The passages a finished answer cites, and the citations that bind them."""

    sources: tuple[RetrievedSource, ...]
    citations: CitationsPayload


def cited_passages(draft: FinishAnswer, passages: Sequence[RetrievedSource]) -> CitedPassages:
    """The passages the draft's blocks cite, numbered from 1 in the order the draft cites them.

    Each search numbers its own passages from 1, so the markers of two searches
    collide. The coordinator model cites passage ids, never markers, so the turn
    numbers only its cited passages, once. A passage the draft does not cite is
    not a source of the answer. With no cited passage the answer carries no
    source and its citations stay unparsed.
    """
    by_id = {p.id: p for p in passages if len(p.id) <= _MAX_CITED_ID_CHARS}
    cited_ids = dict.fromkeys(
        eid for block in draft.blocks for eid in block.evidence_ids if eid in by_id
    )
    numbered = list(enumerate(cited_ids, start=1))
    return CitedPassages(
        sources=tuple(replace(by_id[eid], marker=number) for number, eid in numbered),
        citations=CitationsPayload(
            parsed=bool(numbered),
            cited=[CitedSourceRef(marker=number, id=eid) for number, eid in numbered],
        ),
    )


def restored_sources(outcomes: Sequence[ActionOutcome]) -> tuple[SelectedSources, ...]:
    """Every earlier answer the turn's explain_sources actions restored, in order."""
    return tuple(o.result for o in outcomes if isinstance(o.result, SelectedSources))


def restored_exchange_ids(outcomes: Sequence[ActionOutcome]) -> tuple[str, ...]:
    """The exchanges the turn restored, at most the eight the wire carries."""
    return tuple(e for s in restored_sources(outcomes) for e in s.exchange_ids)[:8]


def restored_refs(outcomes: Sequence[ActionOutcome]) -> tuple[str, ...]:
    """The restore references of the answers the turn restored; a restore re-checks each."""
    return tuple(r for s in restored_sources(outcomes) for r in s.restore_refs)


def own_turn_result(outcomes: Sequence[ActionOutcome]) -> TurnResult:
    """The turn result of a finished answer the coordinator wrote itself.

    Every finished draft keeps one, so a reload restores the text as shown, in its
    own thread only; an answer over passages names the search.
    """
    return TurnResult(
        outcome_type="answered",
        completeness="full",
        trusted=True,
        selected=("document_search",) if retrieved_passages(outcomes) else (),
        omissions=(),
        components=(),
    )


# A stop is never an answer: no finished draft stands behind its copy.
_STOPPED_TURN = TurnResult(
    outcome_type="incomplete",
    completeness="none",
    trusted=False,
    selected=(),
    omissions=(),
    components=(),
)


def stop_turn_result(outcomes: Sequence[ActionOutcome]) -> TurnResult | None:
    """A stop's turn result: none over a failed query, whose own outcome stands;
    incomplete and untrusted otherwise."""
    bq = committed_business_result(outcomes)
    if bq is not None and bq.disposition != "answered":
        return None
    return _STOPPED_TURN


async def publish_answer_draft(
    draft: FinishAnswer,
    observations: Sequence[Observation],
    *,
    outcomes: Sequence[ActionOutcome],
    answer_mode: Literal["explanation", "direct"] | None,
    budget: TurnBudget,
) -> Answered | FixedMessage:
    """Validate model answer draft and publish canonical Answered or FixedMessage outcome."""
    is_expired = False
    try:
        budget.check_not_expired()
        if getattr(budget, "remaining_seconds", math.inf) <= 0:
            is_expired = True
    except Exception:
        is_expired = True

    ask_bq_result = committed_business_result(outcomes)
    wire_outcome: BusinessQueryWireOutcome | None = (
        ask_bq_result.business_query if ask_bq_result is not None else None
    )
    envelope: UnifiedResultEnvelope | None = None

    if wire_outcome:
        envelope = wire_outcome.envelope

    known_evidence_ids: set[str] = set()
    known_value_refs: dict[str, ValueRef] = {}

    for obs in observations:
        known_evidence_ids.update(obs.evidence_ids)
        for v in obs.values:
            known_value_refs[v.id] = v

    draft_valid = not is_expired

    if draft_valid:
        for block in draft.blocks:
            # Reject SQL or internal schema in draft
            if _SQL_RE.search(block.text):
                draft_valid = False
                break

            # Reject invented evidence IDs
            for eid in block.evidence_ids:
                if eid not in known_evidence_ids:
                    draft_valid = False
                    break
            if not draft_valid:
                break

            # Check value_refs 1-to-1 matching slots
            slots = tuple(re.findall(r"\{\{value:(.*?)\}\}", block.text))
            if slots != tuple(block.value_refs):
                draft_valid = False
                break

            # Check unknown value refs
            for vid in block.value_refs:
                if vid not in known_value_refs:
                    draft_valid = False
                    break
            if not draft_valid:
                break

            # Check unbound numeric claims in evidence blocks
            if block.claim_type == "evidence":
                masked_text = re.sub(r"\{\{value:[^}]+\}\}", "", block.text)
                if re.search(r"\b\d+(\.\d+)?%", masked_text) or re.search(r"\$\d+", masked_text):
                    draft_valid = False
                    break

    if draft_valid:
        rendered_parts: list[str] = []
        for block in draft.blocks:
            rendered = re.sub(
                r"\{\{value:(.*?)\}\}",
                lambda m: known_value_refs[m.group(1)].formatted,
                block.text,
            )
            rendered_parts.append(rendered)
        answer_text = "\n\n".join(rendered_parts)

        if wire_outcome is not None:
            turn_result = TurnResult(
                outcome_type="answered",
                completeness="full",
                trusted=True,
                selected=("business_query",),
                omissions=(),
                components=(),
            )
            return Answered(
                answer_text=answer_text,
                sources=(),
                query_type="sql",
                citations=CitationsPayload(parsed=True, cited=[]),
                stage_models=StageModelAccumulator(),
                final_producer_purpose=ModelPurpose.coordinator,
                business_query=wire_outcome,
                bq=ask_bq_result,
                turn_result=turn_result,
                presentation=envelope.presentation if envelope else None,
            )
        else:
            has_docs = any(o.kind == "search_documents" for o in outcomes)
            turn_result = TurnResult(
                outcome_type="answered",
                completeness="full",
                trusted=True,
                selected=("document_search",) if has_docs else (),
                omissions=(),
                components=(),
            )
            return Answered(
                answer_text=answer_text,
                sources=(),
                query_type="document" if has_docs else "general",
                citations=CitationsPayload(parsed=True, cited=[]),
                stage_models=StageModelAccumulator(),
                final_producer_purpose=ModelPurpose.coordinator,
                turn_result=turn_result,
            )

    # Narrative validation failed or budget expired
    if wire_outcome is not None:
        turn_result = TurnResult(
            outcome_type="answered",
            completeness="partial",
            trusted=False,
            selected=("business_query",),
            omissions=(),
            components=(),
        )
        return Answered(
            answer_text=NARRATIVE_UNAVAILABLE_MESSAGE,
            sources=(),
            query_type="sql",
            citations=CitationsPayload(parsed=True, cited=[]),
            stage_models=StageModelAccumulator(),
            final_producer_purpose=ModelPurpose.coordinator,
            business_query=wire_outcome,
            bq=ask_bq_result,
            turn_result=turn_result,
            presentation=envelope.presentation if envelope else None,
        )

    return FixedMessage(
        message=NARRATIVE_UNAVAILABLE_MESSAGE,
        query_type="general",
        stage_models=StageModelAccumulator(),
    )
