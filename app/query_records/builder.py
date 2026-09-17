"""Build Query Record rows from terminal snapshots (S1b)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.pricing.pricer import price_tokens
from app.query_records.content import prepare_question_columns, subject_digest
from app.query_records.context import TerminalSnapshot
from app.query_records.manifest_hash import manifest_hash
from app.query_records.types import QueryRecordData
from app.telemetry.correlation import current_thread_id


def _resolve_manifest_hash() -> str | None:
    path = settings.query_record_manifest_path.strip()
    if not path:
        return None
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return None
    return manifest_hash(manifest_path)


def _request_manifest_hash(snapshot: TerminalSnapshot) -> str | None:
    """Return the verified request policy digest in the storage wire shape."""
    value = (snapshot.principal.manifest_hash or "").removeprefix("sha256:")
    return value if len(value) == 64 else None


def _resolve_bq_trace_json(snapshot: TerminalSnapshot) -> str | None:
    data: dict[str, Any] = {}
    if snapshot.bq_trace_json:
        try:
            parsed = json.loads(snapshot.bq_trace_json)
            if isinstance(parsed, dict):
                data = parsed
        except (TypeError, ValueError):
            pass

    if snapshot.planner_ms is not None and "planner_ms" not in data:
        data["planner_ms"] = snapshot.planner_ms
    if snapshot.sql_ms is not None and "sql_ms" not in data:
        data["sql_ms"] = snapshot.sql_ms
    if snapshot.latency_ms is not None and "latency_ms" not in data:
        data["latency_ms"] = snapshot.latency_ms
    if snapshot.first_activity_ms is not None and "first_activity_ms" not in data:
        data["first_activity_ms"] = snapshot.first_activity_ms
        data["first_progress_event_ms"] = snapshot.first_activity_ms
    elif "first_activity_ms" in data and "first_progress_event_ms" not in data:
        data["first_progress_event_ms"] = data["first_activity_ms"]
    elif "first_progress_event_ms" in data and "first_activity_ms" not in data:
        data["first_activity_ms"] = data["first_progress_event_ms"]

    if snapshot.first_row_ms is not None and "first_row_ms" not in data:
        data["first_row_ms"] = snapshot.first_row_ms
    if snapshot.completion_ms is not None and "completion_ms" not in data:
        data["completion_ms"] = snapshot.completion_ms
    if snapshot.deadline_remaining_ms is not None and "deadline_remaining_ms" not in data:
        data["deadline_remaining_ms"] = snapshot.deadline_remaining_ms
    if snapshot.repair_count is not None and "planner_repair_count" not in data:
        data["planner_repair_count"] = snapshot.repair_count
    if snapshot.rows_returned is not None and "rows_returned" not in data:
        data["rows_returned"] = snapshot.rows_returned
    if snapshot.failure_layer is not None and "failure_layer" not in data:
        data["failure_layer"] = snapshot.failure_layer
    if snapshot.trace_completeness is not None and "trace_completeness" not in data:
        data["trace_completeness"] = snapshot.trace_completeness

    if not data:
        return snapshot.bq_trace_json
    return json.dumps(data, default=str)


def _returned_row_count_from_bq_trace(
    trace_json: str | None, snapshot: TerminalSnapshot | None = None
) -> int | None:
    """Project the executor's bounded row count into the query-record index."""
    if snapshot and snapshot.rows_returned is not None:
        return snapshot.rows_returned
    if not trace_json:
        return None
    try:
        payload = json.loads(trace_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = (
        payload.get("rows_returned")
        if payload.get("rows_returned") is not None
        else payload.get("returned_row_count")
    )
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _stable_error_code_from_bq_trace(trace_json: str | None) -> str | None:
    """The stable operator-facing reason code for a refused or incomplete turn.

    ``sql_stop_reason`` covers turns that stopped during or after SQL
    execution (budget, timeout, evidence sealing). ``failure_detail`` covers
    turns the planner refused before SQL ever ran (unsupported), where the
    module's ``QueryTrace.fail()`` seam is the only place the reason code is
    recorded. Both are the same closed reason-code vocabulary.
    """
    if not trace_json:
        return None
    try:
        payload = json.loads(trace_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("sql_stop_reason") or payload.get("failure_detail")
    return value if isinstance(value, str) and value else None


def _timeout_flag(snapshot: TerminalSnapshot) -> bool | None:
    if snapshot.timeout is not None:
        return snapshot.timeout
    if snapshot.stable_error_code == "timeout":
        return True
    return None


def build_query_record(snapshot: TerminalSnapshot) -> QueryRecordData:
    """Map a terminal snapshot to an insert payload."""
    question_cols = prepare_question_columns(
        question=snapshot.question,
        raw_capture_enabled=settings.query_record_raw_capture_enabled,
    )
    billing_commit = settings.query_record_billing_commit.strip() or None
    rag_commit = settings.query_record_rag_commit.strip() or None
    prompt_versions: str | None = None
    if snapshot.prompt_version:
        prompt_versions = json.dumps({"router": snapshot.prompt_version})

    priced = price_tokens(
        model=snapshot.model,
        input_tokens=snapshot.input_tokens,
        output_tokens=snapshot.output_tokens,
        reasoning_tokens=snapshot.reasoning_tokens,
    )

    resolved_trace_json = _resolve_bq_trace_json(snapshot)

    return QueryRecordData(
        correlation_id=snapshot.correlation_id,
        project_id=settings.query_record_project_id,
        environment=settings.environment,
        **question_cols,
        billing_commit=billing_commit,
        rag_commit=rag_commit,
        service_versions=json.dumps({"api": settings.api_version}),
        manifest_hash=_request_manifest_hash(snapshot) or _resolve_manifest_hash(),
        prompt_versions=prompt_versions,
        record_dispatch_mode=None,
        filter_mode=None,
        analytics_mode=None,
        conversation_mode="enabled" if settings.conversation_enabled else "disabled",
        thread_id=current_thread_id(),
        citation_mode=None,
        subject_digest=subject_digest(str(snapshot.principal.user_id)),
        role_class=snapshot.principal.role,
        context_mode=snapshot.context_mode,
        requested_route=snapshot.requested_route,
        effective_route=snapshot.effective_route or snapshot.query_type,
        terminal_outcome=snapshot.terminal_outcome,
        completion_latency_ms=snapshot.completion_latency_ms(),
        ui_first_text_ms=None,
        cancelled=snapshot.cancelled,
        retry_count=0,
        model=snapshot.model,
        provider=snapshot.provider,
        input_tokens=snapshot.input_tokens,
        output_tokens=snapshot.output_tokens,
        reasoning_tokens=snapshot.reasoning_tokens,
        estimated_usd=priced.estimated_usd,
        cost_status=priced.cost_status,
        price_table_version=priced.price_table_version,
        resolver_query_id=snapshot.resolver_query_id,
        resolver_disposition=snapshot.resolver_disposition,
        row_count=_returned_row_count_from_bq_trace(resolved_trace_json, snapshot),
        stable_error_code=(
            snapshot.stable_error_code or _stable_error_code_from_bq_trace(resolved_trace_json)
        ),
        timeout=_timeout_flag(snapshot),
        bq_trace_json=resolved_trace_json,
    )
