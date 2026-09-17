"""Tests for the Ask AI v2 evaluation result artifact validator."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from evals.prod_ask_e2e.result_artifact import (
    CANONICAL_V2_CASE_IDS,
    EvalResultArtifact,
    ServerTimingMetrics,
    UiTimingMetrics,
    validate_eval_result_artifact,
)


def _make_valid_case_result(case_id: str) -> dict[str, Any]:
    """Create a minimal valid live case result for testing."""
    return {
        "case_id": case_id,
        "status": "passed",
        "dimensions": ["accuracy", "routing"],
        "evidence_layer": "browser",
        "producer": "v2_sse",
        "environment_hash": "env-hash-123",
        "oracle": {
            "kind": "sql_oracle",
            "source": "ai_v1_bq_job_fact",
            "target": "job count",
            "statement_digest": "sha256:abc123digest",
            "expected_hash": "sha256:def456hash",
        },
        "evidence_paths": {
            "screenshot_path": f"evidence/screenshots/{case_id}.png",
            "network_log_path": f"evidence/network/{case_id}.jsonl",
            "console_log_path": "evidence/console.log",
            "query_record_id": f"qr-{case_id}",
        },
        "server_timings": {
            "planner_ms": 120,
            "sql_ms": 45,
            "first_activity_ms": 25,
            "first_progress_ms": 25,
            "first_row_ms": 210,
            "completion_latency_ms": 1450,
            "deadline_ms": 25000,
            "clock_source": "server_monotonic",
        },
        "ui_timings": {
            "ui_first_activity_ms": 35,
            "ui_first_progress_ms": 35,
            "ui_first_row_ms": 230,
            "ui_completion_latency_ms": 1500,
            "clock_source": "browser_performance_now",
        },
        "trace": {
            "correlation_id": f"corr-{case_id}",
            "run_id": f"0000000000000000000000000000{case_id[-4:].replace('-', '0')}",
            "invocation_count": 1,
            "reasoning_count": 1,
            "trace_completeness": True,
            "evidence_digest": "sha256:789digest",
            "answer_query_id": f"aqid-{case_id}",
        },
        "passed": True,
        "verdict": "pass",
    }


def _make_valid_artifact_payload() -> dict[str, Any]:
    """Create a complete artifact dictionary containing all 25 canonical v2 IDs."""
    cases = [_make_valid_case_result(cid) for cid in sorted(CANONICAL_V2_CASE_IDS)]
    return {
        "artifact_version": "2.0.0",
        "suite_id": "prod-ask-e2e",
        "suite_version": "2.0.0",
        "environment_hash": "env-hash-123",
        "created_at_utc": "2026-08-28T14:00:00Z",
        "cases": cases,
        "security_gates": [
            {
                "gate_name": "gate_c_fail_closed",
                "passed": True,
                "verdict": "pass",
                "evidence_path": "evidence/gate_c.json",
                "details": "All Gate C preconditions verified.",
            }
        ],
        "parity_gates": [
            {
                "gate_name": "metric_parity_cross_store",
                "passed": True,
                "verdict": "pass",
                "evidence_path": "evidence/metric_parity.json",
                "details": "Query Record and billing receipts match by correlation ID.",
            }
        ],
        "summary": {
            "total_cases": 25,
            "passed_cases": 25,
            "failed_cases": 0,
        },
    }


def test_canonical_v2_case_ids_has_exact_25_items():
    assert len(CANONICAL_V2_CASE_IDS) == 25
    expected = {
        "fu-01",
        "fu-02",
        "hp-02",
        "pg-01",
        "sq-01",
        "hp-02b",
        "cl-01",
        "cl-02",
        "cl-03",
        "cl-04",
        "cl-05",
        "rt-02",
        "str-01",
        "str-02",
        "str-03",
        "str-04",
        "str-05",
        "act-01",
        "act-02",
        "act-03",
        "act-04",
        "obs-01",
        "obs-02",
        "obs-03",
        "obs-04",
    }
    assert CANONICAL_V2_CASE_IDS == expected


def test_valid_25_case_result_artifact_passes_validation():
    payload = _make_valid_artifact_payload()
    artifact = validate_eval_result_artifact(payload)
    assert isinstance(artifact, EvalResultArtifact)
    assert len(artifact.cases) == 25
    assert len(artifact.security_gates) == 1
    assert len(artifact.parity_gates) == 1
    assert artifact.security_gates[0].gate_name == "gate_c_fail_closed"
    assert artifact.parity_gates[0].gate_name == "metric_parity_cross_store"


def test_rejects_missing_case_ids():
    payload = _make_valid_artifact_payload()
    # Remove fu-01 and act-03
    payload["cases"] = [c for c in payload["cases"] if c["case_id"] not in ("fu-01", "act-03")]
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    assert "missing canonical case IDs" in str(excinfo.value)
    assert "fu-01" in str(excinfo.value)
    assert "act-03" in str(excinfo.value)


def test_rejects_extra_and_unknown_case_ids():
    payload = _make_valid_artifact_payload()
    payload["cases"].append(_make_valid_case_result("unknown-case-99"))
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    assert "unexpected case IDs" in str(excinfo.value)
    assert "unknown-case-99" in str(excinfo.value)


def test_rejects_sample_prefixed_case_ids():
    payload = _make_valid_artifact_payload()
    payload["cases"][0] = _make_valid_case_result("sample-01")
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    val_str = str(excinfo.value)
    assert "unexpected case IDs" in val_str or "missing canonical case IDs" in val_str


def test_rejects_duplicate_case_ids():
    payload = _make_valid_artifact_payload()
    payload["cases"][-1] = _make_valid_case_result("fu-01")
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    val_str = str(excinfo.value)
    assert "duplicate case ID" in val_str or "missing canonical case IDs" in val_str


@pytest.mark.parametrize(
    "invalid_status",
    ["skipped", "not_run", "sampled", "sample", "mock", "placeholder"],
)
def test_rejects_non_live_status(invalid_status: str):
    payload = _make_valid_artifact_payload()
    payload["cases"][0]["status"] = invalid_status
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    val_str = str(excinfo.value).lower()
    assert "live result" in val_str or "input should be" in val_str


def test_rejects_fake_gate_ids_in_cases_list():
    payload = _make_valid_artifact_payload()
    payload["cases"][0]["case_id"] = "sec-01"
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    err = str(excinfo.value)
    assert "unexpected case IDs" in err or "missing canonical case IDs" in err or "gate" in err


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "raw_prompt",
        "prompt",
        "response_content",
        "response_body",
        "raw_response",
        "reasoning",
        "reasoning_content",
        "raw_reasoning",
        "thought",
        "thinking",
        "chain_of_thought",
        "raw_sql",
        "sql_statement",
        "tool_arguments",
        "tool_args",
        "secret",
        "api_key",
        "password",
        "token",
        "policy_body",
        "policy_internals",
        "hidden_prompt",
    ],
)
def test_rejects_raw_sensitive_fields_leak(forbidden_key: str):
    payload = _make_valid_artifact_payload()
    payload["cases"][0][forbidden_key] = "sensitive information leak"
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    val_str = str(excinfo.value).lower()
    assert "forbidden" in val_str or "extra" in val_str or forbidden_key in str(excinfo.value)


def test_rejects_nested_sensitive_fields():
    payload = _make_valid_artifact_payload()
    payload["cases"][0]["oracle"]["raw_prompt"] = "nested prompt leak"
    with pytest.raises(ValidationError) as excinfo:
        validate_eval_result_artifact(payload)
    val_str = str(excinfo.value).lower()
    assert "forbidden" in val_str or "raw_prompt" in str(excinfo.value) or "extra" in val_str


def test_requires_server_and_ui_timing_sources():
    server_timing = ServerTimingMetrics(
        planner_ms=10,
        sql_ms=20,
        first_activity_ms=5,
        first_progress_ms=5,
        first_row_ms=50,
        completion_latency_ms=100,
        deadline_ms=25000,
        clock_source="server_monotonic",
    )
    ui_timing = UiTimingMetrics(
        ui_first_activity_ms=15,
        ui_first_progress_ms=15,
        ui_first_row_ms=65,
        ui_completion_latency_ms=120,
        clock_source="browser_performance_now",
    )
    assert server_timing.clock_source == "server_monotonic"
    assert ui_timing.clock_source == "browser_performance_now"
    assert server_timing.completion_latency_ms != ui_timing.ui_completion_latency_ms
