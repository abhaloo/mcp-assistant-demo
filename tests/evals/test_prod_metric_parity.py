"""Tests for Ask AI v2 production metric parity and zero raw payload exposure."""

from __future__ import annotations

import json

from app.auth import Principal
from app.business_query.outcomes import TimingReceipt
from app.business_query.wire.trace import QueryTrace
from app.config import settings
from app.query_records.builder import build_query_record
from app.query_records.context import TerminalSnapshot
from app.query_records.model import ALWAYS_ON_TEXT_COLUMNS
from app.services.business_query_telemetry import _build_bq_trace_json
from app.telemetry.invocation_ledger import ModelInvocationRecord


def _principal() -> Principal:
    return Principal(user_id=202, role="finance", permissions=["view finance"], department_id=None)


def test_prod_metric_parity_across_four_representations():
    """Join one correlation ID across Query Record, Invocation Row, and artifacts.

    Assert exact metric parity across all server-owned fields.
    """
    cid = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
    model_name = "gpt-5.6-luna"
    provider_name = "azure"
    planner_duration = 145.0
    sql_duration = 52.0
    total_latency = 197.0
    first_activity = 35.0
    first_row = 90.0
    deadline_rem = 24803.0
    repair_cnt = 0
    rows_ret = 8
    in_tok = 1200
    out_tok = 350
    reason_tok = 200

    # 1. QueryTrace & Eval Artifact (as_dict)
    trace = QueryTrace(
        correlation_id=cid,
        deployment=model_name,
        provider=provider_name,
        planner_ms=planner_duration,
        sql_ms=sql_duration,
        total_ms=total_latency,
        first_activity_ms=first_activity,
        first_progress_ms=first_activity,
        first_row_ms=first_row,
        deadline_remaining_ms=deadline_rem,
        planner_repair_count=repair_cnt,
        rows_returned=rows_ret,
        total_row_count=rows_ret,
        tokens_prompt=in_tok,
        tokens_completion=out_tok,
        tokens_reasoning=reason_tok,
        completeness="complete",
        terminal_reason="answered",
        sql="SELECT * FROM invoices WHERE customer_name = ?",
        sql_statement_full="SELECT * FROM invoices WHERE customer_name = 'Confidential Corp'",
    )
    eval_artifact = trace.as_dict()

    # 2. bq_trace_json block
    bq_trace_str = _build_bq_trace_json(trace)

    # 3. TerminalSnapshot & Query Record Data
    snapshot = TerminalSnapshot(
        correlation_id=cid,
        question="Find invoices for customer",
        principal=_principal(),
        terminal_outcome="success",
        run_outcome="completed",
        query_type="business_query",
        requested_route="business_query",
        effective_route="business_query",
        model=model_name,
        provider=provider_name,
        input_tokens=in_tok,
        output_tokens=out_tok,
        reasoning_tokens=reason_tok,
        bq_trace_json=bq_trace_str,
    )
    query_record = build_query_record(snapshot)

    # 4. Model Invocation Record
    invocation_row = ModelInvocationRecord(
        scope="prod",
        correlation_id=cid,
        purpose="record_reasoning",
        route_key="business_query",
        model=model_name,
        provider=provider_name,
        request_messages="[REDACTED]",
        response_content=None,
        reasoning_content=None,
        input_tokens=in_tok,
        output_tokens=out_tok,
        reasoning_tokens=reason_tok,
        latency_ms=int(planner_duration),
        estimated_usd=query_record.estimated_usd,
        cost_status=query_record.cost_status,
    )

    # 5. Billing Timing Receipt
    billing_receipt = TimingReceipt(
        planner_ms=planner_duration,
        sql_ms=sql_duration,
        first_progress_ms=first_activity,
        total_ms=total_latency,
        provider=provider_name,
        deployment=model_name,
        terminal_reason="answered",
    )

    # Assert Parity across all 4 representations:
    # Model & Provider
    assert query_record.model == model_name
    assert invocation_row.model == model_name
    assert eval_artifact["deployment"] == model_name
    assert billing_receipt.deployment == model_name

    assert query_record.provider == provider_name
    assert invocation_row.provider == provider_name
    assert eval_artifact["provider"] == provider_name
    assert billing_receipt.provider == provider_name

    # Token Usage & Cost
    assert query_record.input_tokens == in_tok
    assert invocation_row.input_tokens == in_tok
    assert eval_artifact["tokens_prompt"] == in_tok

    assert query_record.output_tokens == out_tok
    assert invocation_row.output_tokens == out_tok
    assert eval_artifact["tokens_completion"] == out_tok

    assert query_record.reasoning_tokens == reason_tok
    assert invocation_row.reasoning_tokens == reason_tok
    assert eval_artifact["tokens_reasoning"] == reason_tok

    assert query_record.cost_status == invocation_row.cost_status == "complete"
    assert query_record.estimated_usd == invocation_row.estimated_usd

    # Planner & SQL Timing
    assert query_record.planner_ms == planner_duration
    assert eval_artifact["planner_ms"] == planner_duration
    assert billing_receipt.planner_ms == planner_duration

    assert query_record.sql_ms == sql_duration
    assert eval_artifact["sql_ms"] == sql_duration
    assert billing_receipt.sql_ms == sql_duration

    # Activity & Row Milestones
    assert query_record.first_activity_ms == int(first_activity)
    assert query_record.first_progress_event_ms == int(first_activity)
    assert eval_artifact["first_activity_ms"] == first_activity
    assert billing_receipt.first_progress_ms == first_activity

    assert query_record.first_row_ms == int(first_row)
    assert eval_artifact["first_row_ms"] == first_row

    # Total Latency / Completion / Deadline
    assert query_record.latency_ms == int(total_latency)
    assert eval_artifact["total_ms"] == total_latency
    assert billing_receipt.total_ms == total_latency
    assert query_record.deadline_remaining_ms == int(deadline_rem)

    # Execution outcomes & row counts
    assert query_record.row_count == rows_ret
    assert eval_artifact["rows_returned"] == rows_ret
    assert query_record.repair_count == repair_cnt
    assert eval_artifact["planner_repair_count"] == repair_cnt
    assert query_record.trace_completeness == "complete"
    assert eval_artifact["completeness"] == "complete"


def test_zero_raw_payload_exposure_in_eval_artifacts_and_masked_surfaces(monkeypatch):
    """Verify raw prompt text, reasoning content, and raw SQL literals never leak."""
    monkeypatch.setattr(settings, "query_record_raw_capture_enabled", False)
    cid = "b" * 32
    sensitive_token = "sk-live-supersecrettoken12345"
    sensitive_prompt = f"Show private records using token {sensitive_token}"
    sensitive_sql = f"SELECT balance FROM accounts WHERE token = '{sensitive_token}'"

    trace = QueryTrace(
        correlation_id=cid,
        sql_statement_full=sensitive_sql,
        sql="SELECT balance FROM accounts WHERE token = ?",
    )

    # 1. Eval artifact dictionary check
    eval_dict = trace.as_dict()
    eval_str = json.dumps(eval_dict, default=str)

    assert "sql_statement_full" not in eval_dict
    assert sensitive_token not in eval_str
    assert "?" in eval_dict["sql"]

    # 2. Query Record always-on columns check
    snapshot = TerminalSnapshot(
        correlation_id=cid,
        question=sensitive_prompt,
        principal=_principal(),
        terminal_outcome="success",
        run_outcome="completed",
        bq_trace_json=_build_bq_trace_json(trace),
    )
    record = build_query_record(snapshot)
    record_dict = record.model_dump()

    # raw_question must NOT be in always-on columns
    assert "raw_question" not in ALWAYS_ON_TEXT_COLUMNS
    assert record.raw_question is None

    # Always-on text columns must contain no raw sensitive token
    for col_name in ALWAYS_ON_TEXT_COLUMNS:
        val = record_dict.get(col_name)
        if isinstance(val, str):
            assert sensitive_token not in val, f"Sensitive literal leaked into {col_name}"
