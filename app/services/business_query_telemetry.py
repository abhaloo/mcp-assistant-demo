"""Trace -> telemetry projection for Business Query Ask turns.

Folds the module's per-request ``QueryTrace`` (planner/SQL timings, the full
statement, row counts) and the rich renderer's own token usage into the
Ask-layer ``AskBusinessQueryResult`` -- the turn-record trace block and the
OTel `gen_ai.*` histograms both come from here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING, Any

from app.auth import Principal
from app.business_query.ports import QueryRecordWritePort
from app.core.ask_errors import resolve_production_route
from app.providers.model_purpose import ModelPurpose
from app.query_records.builder import build_query_record
from app.query_records.context import TerminalSnapshot
from app.telemetry.metrics import record_operation_duration, record_token_usage

if TYPE_CHECKING:
    from app.business_query.wire.module import BusinessQueryModule
    from app.business_query.wire.trace import QueryTrace
    from app.services.business_query_service import AskBusinessQueryResult

logger = logging.getLogger(__name__)


async def persist_answered_query_record(
    writer: QueryRecordWritePort,
    *,
    question: str | None,
    principal: Principal,
    correlation_id: str,
    result: AskBusinessQueryResult,
) -> bool:
    """Seal the query-friendly projection before an Answered result is exposed."""
    wire = result.business_query
    envelope = wire.envelope if wire is not None else None
    requested_question = (question or "").strip()
    source_question = (
        envelope.receipt.source_question
        if envelope is not None and not requested_question
        else None
    )
    record_question = source_question or requested_question or "[business query result page]"
    snapshot = TerminalSnapshot(
        correlation_id=correlation_id,
        question=record_question,
        principal=principal,
        terminal_outcome="success" if result.disposition == "answered" else result.disposition,
        run_outcome="completed",
        query_type="business_query",
        requested_route="business_query",
        effective_route="business_query",
        started_at=monotonic(),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        reasoning_tokens=result.reasoning_tokens,
        model=result.model,
        provider=result.provider,
        bq_trace_json=result.bq_trace_json,
    )
    try:
        if envelope is not None:
            await writer.bind_sql_receipt(
                correlation_id,
                envelope.receipt.answer_query_id,
            )
        await writer.write(build_query_record(snapshot))
        return True
    except Exception:
        writer.record_failure()
        logger.warning(
            "Business Query Answered withheld: Query Record projection failed correlation_id=%s",
            correlation_id,
            exc_info=True,
        )
        return False


def _sum_optional_tokens(base: int | None, extra: int | None) -> int | None:
    """Add two optional token counts -- ``None`` only when BOTH are absent."""
    if base is None and extra is None:
        return None
    return (base or 0) + (extra or 0)


def _fold_renderer_usage_into_trace_tokens(
    trace: QueryTrace, renderer_usage: dict[str, int | None] | None
) -> tuple[int | None, int | None, int | None]:
    """Fold the rich renderer's own model call into the turn's token totals
    -- the renderer counts as a model call, and its cost is real spend even
    when a render-guard trip discards the text."""
    input_tokens = trace.tokens_prompt
    output_tokens = trace.tokens_completion
    reasoning_tokens = trace.tokens_reasoning
    if renderer_usage is not None:
        input_tokens = _sum_optional_tokens(input_tokens, renderer_usage.get("input_tokens"))
        output_tokens = _sum_optional_tokens(output_tokens, renderer_usage.get("output_tokens"))
        reasoning_tokens = _sum_optional_tokens(
            reasoning_tokens, renderer_usage.get("reasoning_tokens")
        )
    return input_tokens, output_tokens, reasoning_tokens


def _build_bq_trace_json(
    trace: QueryTrace,
    *,
    sql_stop_reason: str | None = None,
    result: AskBusinessQueryResult | None = None,
) -> str:
    """Turn-record trace block. "sql" is the full statement with values --
    this JSON lives only in the owned Postgres query_records table, never in
    logs, eval JSONL, or LangSmith."""
    answer_query_id = trace.answer_query_id or getattr(result, "answer_query_id", None)
    returned_row_count = (
        trace.rows_returned
        if trace.rows_returned is not None
        else getattr(result, "returned_row_count", None)
    )
    total_row_count = trace.total_row_count
    if total_row_count is None:
        total_row_count = getattr(result, "total_row_count", None)
    truncated = trace.truncated
    if truncated is None:
        truncated = getattr(result, "truncated", None)
    completeness = trace.completeness or getattr(result, "completeness", None)
    continuation_token = trace.continuation_token or getattr(result, "continuation_token", None)
    wire = getattr(result, "business_query", None)
    envelope = getattr(wire, "envelope", None)
    receipt = getattr(envelope, "receipt", None)
    first_activity = (
        trace.first_activity_ms if trace.first_activity_ms is not None else trace.first_progress_ms
    )
    total_latency = (
        trace.total_ms
        if trace.total_ms is not None
        else (
            trace.latency_ms
            if getattr(trace, "latency_ms", None) is not None
            else trace.reconcile_total_ms()
        )
    )
    completion_ms = (
        getattr(trace, "completion_ms", None)
        if getattr(trace, "completion_ms", None) is not None
        else total_latency
    )
    terminal_status = (
        trace.terminal_reason
        or getattr(result, "disposition", None)
        or getattr(result, "outcome", None)
    )
    payload: dict[str, Any] = {
        "sql": trace.sql_statement_full or trace.sql,
        "sql_ms": trace.sql_ms,
        "planner_ms": trace.planner_ms,
        "latency_ms": total_latency,
        "planner_repair_count": trace.planner_repair_count,
        "rows_returned": trace.rows_returned,
        "answer_query_id": answer_query_id,
        "root_answer_query_id": getattr(receipt, "root_answer_query_id", None),
        "plan_fingerprint": getattr(receipt, "plan_fingerprint", None),
        "record_referent_digest": getattr(receipt, "record_referent_digest", None),
        "returned_row_count": returned_row_count,
        "total_row_count": total_row_count,
        "truncated": truncated,
        "completeness": completeness,
        "continuation_token": continuation_token,
        "failure_layer": trace.failure_layer,
        "failure_detail": trace.failure_detail,
        "sql_stop_reason": sql_stop_reason,
        "first_activity_ms": first_activity,
        "first_progress_event_ms": first_activity,
        "first_row_ms": trace.first_row_ms,
        "completion_ms": completion_ms,
        "deadline_remaining_ms": trace.deadline_remaining_ms,
        "terminal_status": terminal_status,
        "trace_completeness": completeness
        or ("complete" if trace.failure_layer is None else "incomplete"),
    }
    if trace.sub_queries:
        payload["sub_queries"] = [snap.as_ledger_dict() for snap in trace.sub_queries]
        payload["plan_fingerprint"] = trace.sub_queries[0].plan_fingerprint or payload.get(
            "plan_fingerprint"
        )
        payload["rows_returned"] = sum(snap.rows_returned or 0 for snap in trace.sub_queries)
        first_sql = trace.sub_queries[0].sql_statement_full
        if first_sql:
            payload["sql"] = first_sql
        payload["answer_query_id"] = trace.sub_queries[0].answer_query_id or payload.get(
            "answer_query_id"
        )
    return json.dumps(
        payload,
        default=str,
    )


def _bind_wire_receipt_to_trace(trace: QueryTrace, result: AskBusinessQueryResult) -> None:
    """Bind the sealed transport receipt to the Query Record trace.

    The module owns AQID minting and durable event append.  This projection
    runs after that boundary, so it copies the same receipt rather than
    minting or inferring a second identifier.
    """
    wire = getattr(result, "business_query", None)
    envelope = getattr(wire, "envelope", None)
    if envelope is None:
        return
    trace.answer_query_id = envelope.receipt.answer_query_id
    trace.rows_returned = envelope.returned_row_count
    trace.total_row_count = envelope.total_row_count
    trace.truncated = envelope.returned_row_count < envelope.total_row_count
    trace.completeness = envelope.result_completeness
    if envelope.next_page_action is not None:
        trace.continuation_token = envelope.next_page_action.cursor


def _emit_bq_otel_metrics(
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    trace: QueryTrace,
) -> None:
    """OTel parity with the RAG path -- purpose-tagged, no
    role/permissions/access_tiers attributes (cardinality discipline; see
    metrics.py)."""
    if input_tokens is not None and output_tokens is not None:
        record_token_usage(
            operation="business_query",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    total_ms = (trace.planner_ms or 0) + (trace.sql_ms or 0)
    if total_ms > 0:
        record_operation_duration(
            operation="business_query", model=model, seconds=total_ms / 1000.0
        )


def apply_query_trace(
    result: AskBusinessQueryResult,
    module: BusinessQueryModule,
    correlation_id: str,
    *,
    renderer_usage: dict[str, int | None] | None = None,
) -> AskBusinessQueryResult:
    trace_for = getattr(module, "trace_for", None)
    if trace_for is None:
        return result
    try:
        trace = trace_for(correlation_id)
    except KeyError:
        return result
    route = resolve_production_route(ModelPurpose.record_reasoning)
    model = route.attested_azure_model_version or route.deployment

    input_tokens, output_tokens, reasoning_tokens = _fold_renderer_usage_into_trace_tokens(
        trace, renderer_usage
    )
    _bind_wire_receipt_to_trace(trace, result)
    bq_trace_json = _build_bq_trace_json(
        trace,
        sql_stop_reason=result.sql_stop_reason,
        result=result,
    )
    _emit_bq_otel_metrics(
        model=model, input_tokens=input_tokens, output_tokens=output_tokens, trace=trace
    )

    return replace(
        result,
        model=model,
        provider=route.provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        bq_trace_json=bq_trace_json,
    )
