"""Shared answer persistence + optional follow-up enrichment for JSON and SSE."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.auth import Principal
from app.business_query.plan import plan_fingerprint
from app.business_query.wire.result_presentation import build_result_presentation
from app.conversation.reference_artifact import ReferenceArtifact
from app.conversation.transcript_models import BqTurnDigest
from app.conversation.transcript_store import new_exchange_id
from app.conversation.turn import PersistedExchange, TurnContext, finalize_exchange
from app.models.tool_results import tool_result_fields
from app.services.ask_outcome import TurnEvidence
from app.services.evidence_snapshots import publish_turn_evidence
from app.services.feedback_tokens import make_feedback_token
from app.services.follow_up_suggestions import (
    FollowUpSuggestionDecision,
    generate_follow_up_suggestions,
    generate_follow_up_suggestions_from_bq,
    normalize_follow_up_suggestions,
    select_follow_up_suggestions,
)
from app.services.record_intent import RecordAggregateContinuation
from app.telemetry.metrics import record_follow_up_suggestion_decision

if TYPE_CHECKING:
    from app.services.business_query_service import AskBusinessQueryResult

__all__ = [
    "bq_digest_for_persist",
    "finalize_answer",
    "generate_follow_up_suggestions",
    "generate_follow_up_suggestions_from_bq",
    "persist_and_enrich",
    "timeout_digest_for_persist",
]


@dataclass(frozen=True)
class AnswerFinalizeResult:
    persisted: PersistedExchange
    follow_up_suggestions: list[str]
    trace_id: str | None = None
    feedback_token: str | None = None
    restore_ref: str | None = None


def bq_digest_for_persist(
    *,
    query_type: str | None,
    bq: AskBusinessQueryResult | None,
) -> BqTurnDigest | None:
    """Build a transcript digest for structured turns with a plan; else None.

    Carries plan metadata, row count, optional scalar, and receipt title only —
    never SQL and never result rows.
    """
    if query_type != "structured" or bq is None or bq.plan is None:
        return None
    plan = bq.plan
    envelope = bq.business_query.envelope if bq.business_query is not None else None
    row_count = envelope.total_row_count if envelope is not None else 0
    raw_scalar = None
    if envelope is not None and len(envelope.rows) == 1 and len(envelope.rows[0]) == 1:
        raw_scalar = next(iter(envelope.rows[0].values()))
    scalar_value = None if raw_scalar is None else str(raw_scalar)
    presentation = (
        envelope.presentation
        if envelope is not None and envelope.presentation is not None
        else build_result_presentation(plan, scalar_value=raw_scalar)
    )
    return BqTurnDigest(
        outcome="answered",
        plan_fingerprint=plan_fingerprint(plan),
        grain=plan.grain,
        measures=list(plan.measures),
        dimensions=list(plan.dimensions),
        row_count=row_count,
        scalar_value=scalar_value,
        receipt_title=presentation.title,
    )


def timeout_digest_for_persist(question: str) -> BqTurnDigest:
    """Build a transcript digest for a timed-out turn (no SQL, no rows)."""
    return BqTurnDigest(
        outcome="timeout",
        plan_fingerprint="",
        grain="scalar",
        measures=[],
        dimensions=[],
        row_count=0,
        scalar_value=None,
        receipt_title=question[:120],
    )


async def persist_and_enrich(
    *,
    ctx: TurnContext,
    principal: Principal,
    question: str,
    answer: str,
    follow_up_suggestions: list[str] | None = None,
    reference_artifact: ReferenceArtifact | None = None,
    aggregate_continuation: RecordAggregateContinuation | None = None,
    bq_digest: BqTurnDigest | None = None,
    run_id: str | None = None,
    evidence: TurnEvidence | None = None,
    answer_mode: Literal["explanation", "direct"] | None = None,
    source_exchange_ids: Sequence[str] = (),
    conversation_subject: str | None = None,
) -> AnswerFinalizeResult:
    """Persist one exchange and make a deterministic terminal suggestion decision.

    No LLM suggestion call is awaited before JSON return or SSE ``done``. A
    caller may provide a precomputed deterministic list (currently records-only
    presentation); ordinary document, SQL, failure, and independent requests
    use the explicit no-suggestion mode while R3's capability-card API settles.

    ``reference_artifact`` and ``bq_digest`` pass straight through to
    ``finalize_exchange``; default None keeps every existing caller
    byte-identical until a production site computes a real value.
    """
    # The exchange id is minted here so the snapshot and the transcript row that
    # names its restore_ref bind to the same exchange (spec §7).
    exchange_id = new_exchange_id() if ctx.thread_id is not None else None
    restore_ref: str | None = None
    if evidence is not None and exchange_id is not None and run_id is not None:
        restore_ref = await publish_turn_evidence(
            evidence,
            principal=principal,
            thread_id=ctx.thread_id,
            run_id=run_id,
            exchange_id=exchange_id,
        )
    tool_results = (
        tool_result_fields(evidence.turn_result, restore_ref=restore_ref)
        if evidence is not None
        else None
    )
    persisted = await finalize_exchange(
        ctx,
        principal,
        question,
        answer,
        reference_artifact=reference_artifact,
        aggregate_continuation=aggregate_continuation,
        bq_digest=bq_digest,
        exchange_id=exchange_id,
        tool_results=tool_results,
        answer_mode=answer_mode,
        source_exchange_ids=source_exchange_ids,
        conversation_subject=conversation_subject,
    )
    if persisted.exchange_id is None:
        # No transcript row names the snapshot; it ages out unreferenced.
        restore_ref = None
    started = time.perf_counter()
    if follow_up_suggestions is None:
        decision = select_follow_up_suggestions(question, answer, ctx.history)
        suggestions = decision.suggestions
    else:
        suggestions = normalize_follow_up_suggestions(follow_up_suggestions)
        decision = FollowUpSuggestionDecision(mode="page_context", suggestions=suggestions)
    record_follow_up_suggestion_decision(
        mode=decision.mode,
        seconds=time.perf_counter() - started,
    )

    trace_id: str | None = None
    feedback_token: str | None = None
    if persisted.exchange_id is not None and run_id is not None:
        trace_id = run_id
        feedback_token = make_feedback_token(run_id, principal)

    return AnswerFinalizeResult(
        persisted=persisted,
        follow_up_suggestions=suggestions,
        trace_id=trace_id,
        feedback_token=feedback_token,
        restore_ref=restore_ref,
    )


finalize_answer = persist_and_enrich
