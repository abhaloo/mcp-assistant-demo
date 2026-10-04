"""Projection of tool turn results and Ask outcomes into consistent transports.

Follows Spec §6 and ADR 0076.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from opentelemetry import trace
from opentelemetry.trace import Span
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.auth import Principal
from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.outcomes import BusinessQueryWireOutcome, UnifiedResultEnvelope
from app.concurrency import llm_slot
from app.conversation.evidence.contracts import (
    NarrativeDependencies,
    RestoredStep,
    RestoredThought,
    RestoredTurn,
    SnapshotBindings,
    SnapshotPayload,
)
from app.core.ask_errors import is_model_route_denial
from app.core.breakers import CircuitBreaker
from app.core.turn_budget import TurnBudget, await_with_budget

if TYPE_CHECKING:
    from app.conversation.evidence.ports import EvidenceSnapshotStore
from app.models.ask_response import Answer
from app.models.ask_v2_events import (
    FollowUpAction,
    InteractionOption,
    TableColumn,
    TextContentKind,
)
from app.models.citations import CitationsPayload, Source
from app.models.record_context import RecordContext
from app.models.result_presentation import ResultPresentation
from app.models.schemas import QueryType
from app.models.sql_provenance import QueryExplanation, SqlProvenance
from app.models.tool_results import TurnResult
from app.prompts.registry import registry
from app.rag.retrieval.document_contracts import DocumentProvenance
from app.services.ask_prepare import prompt_pipeline_for
from app.services.ask_v2_columns import wire_table_columns
from app.services.ask_v2_reason_copy import copy_for_reason
from app.telemetry import emit_circuit_breaker_reject, run_in_thread
from app.telemetry.helpers import record_span_failure_code

logger = logging.getLogger(__name__)


def build_snapshot_bindings(
    *,
    principal: Principal,
    thread_id: str,
    run_id: str,
    exchange_id: str,
    project: str = "default",
    now: datetime | None = None,
    ttl_hours: int = 24,
) -> SnapshotBindings:
    """Construct canonical snapshot bindings for an authorized turn."""
    created = now or datetime.now(tz=UTC)
    expires = created + timedelta(hours=ttl_hours)
    return SnapshotBindings(
        project=project,
        actor_id=str(principal.user_id),
        entity_id=principal.entity_id if principal.entity_id is not None else 0,
        department_id=principal.department_id,
        thread_id=thread_id,
        run_id=run_id,
        exchange_id=exchange_id,
        schema_version=1,
        content_digest=None,
        created_at=created,
        expires_at=expires,
        tombstone=False,
    )


RunStatus = Literal["done", "clarification", "failed", "stopped"]


def _run_status(outcome_type: str) -> RunStatus | None:
    """Map turn outcome type to terminal run status for restored view."""
    if outcome_type == "answered":
        return "done"
    if outcome_type == "clarification_required":
        return "clarification"
    if outcome_type == "cancelled":
        # A turn the person stopped is stopped, complete on the server
        # record, and never reads as a failure.
        return "stopped"
    return "failed"


def build_snapshot_payload(
    *,
    thread_id: str,
    run_id: str,
    exchange_id: str,
    answer_text: str,
    turn_result: TurnResult,
    sources: tuple[Source, ...] | list[Source] = (),
    citations: CitationsPayload | None = None,
    business_query: BusinessQueryWireOutcome | None = None,
    presentation: ResultPresentation | None = None,
    stored_plans: tuple[StoredPlan, ...] | list[StoredPlan] = (),
    document_provenance: tuple[DocumentProvenance, ...] | list[DocumentProvenance] = (),
    narrative_dependencies: NarrativeDependencies | None = None,
    duration_ms: int | None = None,
    run_status: RunStatus | None = None,
    steps: tuple[RestoredStep, ...] = (),
    thoughts: tuple[RestoredThought, ...] = (),
    follow_ups: tuple[FollowUpAction, ...] = (),
    unanswered_part: str | None = None,
) -> SnapshotPayload:
    """Build immutable snapshot payload retaining only authorized visible components."""
    status = run_status if run_status is not None else _run_status(turn_result.outcome_type)
    restored_turn = RestoredTurn(
        thread_id=thread_id,
        run_id=run_id,
        exchange_id=exchange_id,
        answer_text=answer_text,
        sources=tuple(sources),
        citations=citations or CitationsPayload(parsed=False, cited=[]),
        business_query=business_query,
        presentation=presentation,
        turn_result=turn_result,
        run_status=status,
        duration_ms=duration_ms,
        steps=steps,
        thoughts=thoughts,
        follow_ups=follow_ups,
        unanswered_part=unanswered_part,
    )
    return SnapshotPayload(
        restored_turn=restored_turn,
        stored_plans=tuple(stored_plans),
        document_provenance=tuple(document_provenance),
        narrative_dependencies=narrative_dependencies,
    )


async def publish_evidence_snapshot(  # noqa: PLR0913 - the publication seam's typed payload
    store: EvidenceSnapshotStore,
    bindings: SnapshotBindings,
    payload: SnapshotPayload,
    *,
    restore_ref: str | None = None,
    budget: TurnBudget | None = None,
    timeout_seconds: float = 2.0,
) -> str | None:
    """Publish an evidence snapshot within remaining original turn budget.

    Returns the 43-character URL-safe restore_ref on success, or None on failure/timeout.
    A caller-minted reference is the snapshot table's primary key: a replayed insert
    of a sealed reference is read as "already sealed" and never overwrites the row.
    Storage failure or budget exhaustion yields restore_ref=null and leaves the live
    result intact.
    """
    try:
        if budget is not None and budget.is_exhausted:
            logger.warning("Turn budget exhausted before snapshot publication; skipping snapshot")
            return None

        async def _do_put() -> str:
            try:
                if restore_ref is None:
                    return await store.put(bindings, payload)
                return await store.put(bindings, payload, restore_ref=restore_ref)
            except IntegrityError:
                if restore_ref is None:
                    # A store-minted reference cannot collide by construction.
                    raise
                logger.warning(
                    "snapshot already sealed; keeping the stored row: reference=%s", restore_ref
                )
                return restore_ref

        if budget is not None:
            return await await_with_budget(
                _do_put,
                budget,
                ceiling_seconds=timeout_seconds,
            )
        return await asyncio.wait_for(_do_put(), timeout=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - a snapshot failure never fails the answer
        logger.warning("Failed publishing evidence snapshot: %s", type(exc).__name__)
        return None


def record_context_sources(record_context: RecordContext) -> list[Source]:
    """Additive source entries for a validated record context."""
    return [
        Source(
            id=f"record:{r.resource_type}:{r.record_id}",
            content="; ".join(f"{key}: {value}" for key, value in r.fields.items()),
            source_file=f"record:{r.resource_type}:{r.record_id}",
            chunk_index=None,
            marker=None,
            section=None,
            resource_type=r.resource_type,
            record_id=r.record_id,
            label=r.label,
            link_key=r.link_key,
        )
        for r in record_context.records
    ]


def stamp_classify_prompt_version(span: Span, query_type: QueryType) -> None:
    span.set_attribute("prompt.version", registry.version(prompt_pipeline_for(query_type)))


def make_on_classified(correlation_id: str) -> Callable[[Span, QueryType], None]:
    """Build the on_classified hook stamping correlation_id on the classify span."""

    def _on_classified(span: Span, query_type: QueryType) -> None:
        span.set_attribute("correlation_id", correlation_id)
        stamp_classify_prompt_version(span, query_type)

    return _on_classified


def option_detail(choice: Any) -> str | None:
    """Preview text. An explicit detail key wins, including None on resolver radios."""
    if isinstance(choice, dict) and "detail" in choice:
        value = choice.get("detail")
        return value if isinstance(value, str) and value.strip() else None
    if not isinstance(choice, dict):
        fields_set = getattr(choice, "model_fields_set", None)
        if fields_set is not None and "detail" in fields_set:
            value = getattr(choice, "detail", None)
            return value if isinstance(value, str) and value.strip() else None
        rewrite = getattr(choice, "rewrite", None)
        value_prompt = getattr(choice, "value_prompt", None)
    else:
        rewrite, value_prompt = choice.get("rewrite"), choice.get("value_prompt")
    for candidate in (rewrite, value_prompt):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def normalize_interaction_options(choices_raw: Any) -> list[InteractionOption]:
    """Normalize interaction options from raw choices sequence."""
    options: list[InteractionOption] = []
    for i, c in enumerate(choices_raw or []):
        if isinstance(c, dict):
            options.append(
                InteractionOption(
                    id=str(c.get("id", str(i))),
                    label=str(c.get("label", str(c))),
                    detail=option_detail(c),
                    href=c.get("href"),
                )
            )
        elif hasattr(c, "id") and hasattr(c, "label"):
            options.append(
                InteractionOption(
                    id=str(getattr(c, "id")),
                    label=str(getattr(c, "label")),
                    detail=option_detail(c),
                    href=getattr(c, "href", None),
                )
            )
        else:
            options.append(InteractionOption(id=str(c), label=str(c)))
    return options


def content_kind(
    *, table_streamed: bool, producer_kind: TextContentKind | None = None
) -> TextContentKind:
    """The producer's own classing wins; otherwise text accompanying a table is
    its fallback serialization and anything else is narrative."""
    if producer_kind is not None:
        return producer_kind
    return "table_fallback" if table_streamed else "narrative"


def explanation_from_provenance(answer: Answer) -> QueryExplanation | None:
    """The plan explanation the finish policy attached."""
    return answer.sql_provenance.explanation if answer.sql_provenance is not None else None


def as_envelope_mapping(envelope: Any) -> dict[str, Any] | None:
    if envelope is None:
        return None
    if isinstance(envelope, dict):
        return envelope
    dump = getattr(envelope, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return None


def extract_table_data(
    envelope: Any,
) -> tuple[list[dict[str, Any]], list[TableColumn], str | None]:
    """Rows, sealed column schema, and AQID from one result envelope."""
    data = as_envelope_mapping(envelope)
    if data is None:
        return [], [], None
    rows = data.get("rows") or []
    columns = wire_table_columns(list(data.get("columns") or []))
    return rows, columns, data.get("answer_query_id")


def envelope_presentation(envelope: Any) -> ResultPresentation | None:
    """Receipt title and facts the finish policy attached, if valid."""
    data = as_envelope_mapping(envelope)
    if data is None:
        return None
    raw = data.get("presentation")
    if not isinstance(raw, dict):
        return None
    try:
        return ResultPresentation.model_validate(raw)
    except ValidationError as exc:
        logger.warning("v2 result presentation dropped: %d validation errors", exc.error_count())
        return None


def total_row_count(envelope: Any) -> int | None:
    data = as_envelope_mapping(envelope)
    total = data.get("total_row_count") if data is not None else None
    return total if isinstance(total, int) and not isinstance(total, bool) and total >= 0 else None


def result_envelopes(answer: Answer) -> list[UnifiedResultEnvelope]:
    """Envelopes in ordinal order."""
    wire = answer.business_query
    if wire is None:
        return []
    if wire.envelopes:
        return list(wire.envelopes)
    return [wire.envelope] if wire.envelope is not None else []


def coordinator_wrote(text_kind: TextContentKind | None, text: str | None) -> bool:
    """True when the answer text is a finished coordinator draft: that producer
    alone sets text_kind, and its words and offer stay on a failed turn."""
    return text_kind is not None and bool(text)


def offers_follow_ups(outcome_type: str, *, coordinator_wrote_text: bool) -> bool:
    """An answered turn may carry an offer; so may a failed turn the coordinator explained."""
    return outcome_type == "answered" or coordinator_wrote_text


def follow_up_actions(answer: Answer, outcome_type: str) -> list[FollowUpAction]:
    """The follow-ups the terminal frame shows for this result."""
    wrote = coordinator_wrote(answer.text_kind, answer.answer)
    if answer.follow_up_offer is None or not offers_follow_ups(
        outcome_type, coordinator_wrote_text=wrote
    ):
        return []
    return list(answer.follow_up_offer.actions)


def refusal_reason(answer: Answer, outcome_type: str) -> tuple[str | None, str | None]:
    """Reason code and copy for a terminal turn, or (None, None) if answered.

    The copy is None when the coordinator wrote the text: the code still
    reaches the wire, the words stay the coordinator's.
    """
    if outcome_type == "answered":
        return None, None
    wrote = coordinator_wrote(answer.text_kind, answer.answer)
    if answer.reason_code is not None:
        return answer.reason_code, None if wrote else copy_for_reason(
            answer.reason_code, answer.answer
        )
    wire = answer.business_query
    if wire is None or wire.reason_code is None:
        return None, None
    return wire.reason_code, None if wrote else copy_for_reason(wire.reason_code, wire.message)


def terminal_disposition(answer: Answer) -> str:
    """The disposition to report for this turn.

    When TurnResult is present, it is the ONLY v2 disposition source (ADR 0076).
    """
    if answer.turn_result is not None:
        return answer.turn_result.outcome_type
    if answer.business_query is not None and answer.business_query.outcome:
        return str(answer.business_query.outcome)
    return "answered" if answer.answer else "incomplete"


@dataclass(frozen=True)
class BranchResult:
    """Normalized backend output. LangChain's dict shapes stop at the invoker boundary."""

    answer: str
    sources: list[Source]
    cited_markers: list[int] | None = None
    sql_provenance: SqlProvenance | None = None


GuardError = Literal["circuit_open", "retriever_unavailable"]


@dataclass(frozen=True)
class GuardOutcome:
    name: str
    result: BranchResult | None
    error: GuardError | None
    circuit_rejected: bool


async def guarded_call(name: str, breaker: CircuitBreaker, fn, *args) -> GuardOutcome:
    """Run a call through the circuit breaker."""
    if not breaker.can_execute():
        emit_circuit_breaker_reject(breaker_name=breaker.name, state=breaker.state.value)
        return GuardOutcome(name, None, "circuit_open", True)

    try:
        async with llm_slot():
            result = await run_in_thread(fn, *args)
        breaker.record_success()
        return GuardOutcome(name, result, None, False)
    except Exception as exc:
        if not is_model_route_denial(exc):
            breaker.record_failure()
        current = trace.get_current_span()
        if current.is_recording():
            record_span_failure_code(current, "retriever_unavailable")
        return GuardOutcome(name, None, "retriever_unavailable", False)
