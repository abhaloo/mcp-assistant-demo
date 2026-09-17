"""Coordinator answer publication, typed validation, and safe fallback (R5)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Literal

from app.business_query.outcomes import BusinessQueryWireOutcome, UnifiedResultEnvelope
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.conversation.coordinator.action_lifecycle import ActionOutcome
from app.conversation.coordinator.contracts import FinishAnswer, Observation, ValueRef
from app.core.turn_budget import TurnBudget
from app.models.citations import CitationsPayload
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


def committed_business_result(outcomes: Sequence[ActionOutcome]) -> AskBusinessQueryResult | None:
    """The last business result the turn committed, with its wire outcome.

    A finished answer written over that result must carry it forward: the
    evidence snapshot, the restore reference and the tables the panel shows
    all come from this one object, never from the model's text.
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
    """Every passage the turn's document searches returned, in search order.

    A finished answer written over those passages carries them as its
    sources: the panel lists them and the evidence gate grounds the text
    against them.
    """
    passages: list[RetrievedSource] = []
    for outcome in outcomes:
        if isinstance(outcome.result, DocumentSearchResult):
            passages.extend(outcome.result.passages)
    return tuple(passages)


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
