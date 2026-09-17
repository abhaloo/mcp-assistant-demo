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

from app.auth import Principal
from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.outcomes import BusinessQueryWireOutcome
from app.concurrency import llm_slot
from app.conversation.evidence.contracts import (
    NarrativeDependencies,
    RestoredTurn,
    SnapshotBindings,
    SnapshotPayload,
)
from app.core.ask_errors import is_model_route_denial
from app.core.breakers import CircuitBreaker
from app.core.turn_budget import TurnBudget, await_with_budget

if TYPE_CHECKING:
    from app.conversation.evidence.ports import EvidenceSnapshotStore
from app.models.ask_v2_events import (
    FollowUpAction,
    FollowUpOffer,
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
) -> SnapshotPayload:
    """Build immutable snapshot payload retaining only authorized visible components."""
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
    )
    return SnapshotPayload(
        restored_turn=restored_turn,
        stored_plans=tuple(stored_plans),
        document_provenance=tuple(document_provenance),
        narrative_dependencies=narrative_dependencies,
    )


async def publish_evidence_snapshot(
    store: EvidenceSnapshotStore,
    bindings: SnapshotBindings,
    payload: SnapshotPayload,
    *,
    budget: TurnBudget | None = None,
    timeout_seconds: float = 2.0,
) -> str | None:
    """Publish an evidence snapshot within remaining original turn budget.

    Returns the 43-character URL-safe restore_ref on success, or None on failure/timeout.
    Storage failure or budget exhaustion yields restore_ref=null and leaves the live result intact.
    """
    try:
        if budget is not None and budget.is_exhausted:
            logger.warning("Turn budget exhausted before snapshot publication; skipping snapshot")
            return None

        async def _do_put() -> str:
            return await store.put(bindings, payload)

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
    """Preview text for a stall-card choice: rewrite, else value_prompt."""
    if isinstance(choice, dict):
        rewrite, value_prompt = choice.get("rewrite"), choice.get("value_prompt")
    else:
        rewrite = getattr(choice, "rewrite", None)
        value_prompt = getattr(choice, "value_prompt", None)
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
                )
            )
        elif hasattr(c, "id") and hasattr(c, "label"):
            options.append(
                InteractionOption(
                    id=str(getattr(c, "id")),
                    label=str(getattr(c, "label")),
                    detail=option_detail(c),
                )
            )
        else:
            options.append(InteractionOption(id=str(c), label=str(c)))
    return options


def content_kind(*, table_streamed: bool) -> TextContentKind:
    """Text accompanying a table is fallback serialization; otherwise narrative."""
    return "table_fallback" if table_streamed else "narrative"


def explanation_from_provenance(res: dict[str, Any]) -> QueryExplanation | None:
    """The plan explanation the finish policy attached, if valid."""
    provenance = res.get("sql_provenance")
    if not isinstance(provenance, dict):
        return None
    raw = provenance.get("explanation")
    if not isinstance(raw, dict):
        return None
    try:
        return QueryExplanation.model_validate(raw)
    except ValidationError:
        logger.warning("v2 explanation failed validation and was dropped", exc_info=True)
        return None


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


def result_envelopes(
    res: dict[str, Any],
    *,
    wire: BusinessQueryWireOutcome | None = None,
) -> list[Any]:
    """Envelopes in ordinal order."""
    business_query = res.get("business_query")
    if not isinstance(business_query, dict):
        rows = res.get("rows")
        return [{"rows": rows}] if isinstance(rows, list) and rows else []
    if wire is not None:
        if wire.envelopes:
            return list(wire.envelopes)
        if wire.envelope is not None:
            return [wire.envelope]
        return []
    raw = business_query.get("envelopes")
    if isinstance(raw, list) and raw:
        return raw
    envelope = business_query.get("envelope")
    if isinstance(envelope, dict):
        return [envelope]
    if "rows" in business_query:
        return [business_query]
    return []


def follow_up_actions(res: dict[str, Any], outcome_type: str) -> list[FollowUpAction]:
    """Only an answered turn may carry an offer, and only a valid one."""
    raw = res.get("follow_up_offer")
    if outcome_type != "answered" or not isinstance(raw, dict):
        return []
    try:
        return FollowUpOffer.model_validate(raw).actions
    except ValidationError as exc:
        logger.warning("v2 follow-up offer dropped: %d validation errors", exc.error_count())
        return []


def refusal_reason(res: dict[str, Any], outcome_type: str) -> tuple[str | None, str | None]:
    """Reason code and copy for a terminal turn, or (None, None) if answered."""
    if outcome_type == "answered":
        return None, None
    business_query = res.get("business_query")
    if not isinstance(business_query, dict):
        return None, None
    reason_code = business_query.get("reason_code")
    if reason_code is None:
        return None, None
    return reason_code, copy_for_reason(reason_code, business_query.get("message"))


def terminal_disposition(
    res: dict[str, Any],
    *,
    wire: BusinessQueryWireOutcome | None = None,
    turn_result: TurnResult | None = None,
) -> str:
    """The disposition to report for this turn.

    When TurnResult is present, it is the ONLY v2 disposition source (ADR 0076).
    """
    tr = turn_result if turn_result is not None else res.get("turn_result")
    if isinstance(tr, TurnResult):
        return tr.outcome_type
    if wire is not None and wire.outcome:
        return str(wire.outcome)
    business_query = res.get("business_query")
    if isinstance(business_query, dict) and business_query.get("outcome"):
        return str(business_query["outcome"])
    outcome = res.get("disposition") or res.get("outcome")
    if outcome:
        return str(outcome)
    if res.get("answer") or res.get("answer_text"):
        return "answered"
    return "incomplete"


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
