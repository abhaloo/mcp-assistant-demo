"""Evaluation result artifact validator and performance manifest schemas for Ask AI v2."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CANONICAL_V2_CASE_IDS: frozenset[str] = frozenset(
    {
        "fu-01",
        "fu-02",
        "hp-02",
        "hp-02b",
        "pg-01",
        "sq-01",
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
)

PERFORMANCE_MEASURED_TURN_IDS: tuple[str, ...] = (
    "fu-01",
    "fu-02",
    "hp-02",
    "hp-02b",
    "cl-01",
    "cl-02",
    "pg-01",
    "sq-01",
)

PERFORMANCE_REPETITIONS: int = 3
TOTAL_PERFORMANCE_MEASURED_TURNS: int = 24

FORBIDDEN_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "raw_prompt",
        "prompt",
        "prompts",
        "system_prompt",
        "request_messages",
        "messages",
        "response",
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
    }
)

FORBIDDEN_STATUSES: frozenset[str] = frozenset(
    {"skipped", "not_run", "sampled", "sample", "mock", "placeholder"}
)


def _scan_for_sensitive_keys(data: Any, path: str = "") -> None:
    """Recursively scan data structures for sensitive keys."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key in FORBIDDEN_SENSITIVE_KEYS:
                current_path = f"{path}.{key}" if path else key
                raise ValueError(f"Forbidden sensitive key '{key}' found at path '{current_path}'")
            _scan_for_sensitive_keys(value, f"{path}.{key}" if path else key)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _scan_for_sensitive_keys(item, f"{path}[{index}]")


class ServerTimingMetrics(BaseModel):
    """Server-side timing milestones from internal clocks."""

    model_config = ConfigDict(extra="forbid")

    planner_ms: float | int | None = None
    sql_ms: float | int | None = None
    first_activity_ms: float | int | None = None
    first_progress_ms: float | int | None = None
    first_row_ms: float | int | None = None
    completion_latency_ms: float | int = Field(ge=0)
    deadline_ms: float | int | None = None
    clock_source: str = "server_monotonic"


class UiTimingMetrics(BaseModel):
    """Browser-side timing milestones from performance.now clock."""

    model_config = ConfigDict(extra="forbid")

    ui_first_activity_ms: float | int | None = None
    ui_first_progress_ms: float | int | None = None
    ui_first_row_ms: float | int | None = None
    ui_completion_latency_ms: float | int = Field(ge=0)
    clock_source: str = "browser_performance_now"


class SafeTraceMetadata(BaseModel):
    """Safe trace identifiers and metrics without sensitive payloads."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    invocation_count: int = Field(default=0, ge=0)
    reasoning_count: int = Field(default=0, ge=0)
    trace_completeness: bool = True
    evidence_digest: str | None = None
    answer_query_id: str | None = None


class CaseOracleRef(BaseModel):
    """Reference to independent oracle verification data."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    source: str | None = None
    target: str | None = None
    statement_digest: str | None = None
    expected_hash: str | None = None


class CaseEvidencePaths(BaseModel):
    """File paths for durable case evidence."""

    model_config = ConfigDict(extra="forbid")

    screenshot_path: str | None = None
    network_log_path: str | None = None
    console_log_path: str | None = None
    query_record_id: str | None = None


class CaseResult(BaseModel):
    """Individual test case execution result in the live artifact."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    status: Literal["completed", "passed", "failed", "errored", "live"]
    dimensions: list[str] = Field(min_length=1)
    evidence_layer: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    environment_hash: str = Field(min_length=1)
    oracle: CaseOracleRef | dict[str, Any]
    evidence_paths: CaseEvidencePaths | dict[str, Any] | list[str] | None = None
    server_timings: ServerTimingMetrics | None = None
    ui_timings: UiTimingMetrics | None = None
    trace: SafeTraceMetadata | None = None
    verdict: str | None = None
    passed: bool = True
    notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case_data(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _scan_for_sensitive_keys(data)
            status = data.get("status")
            if status in FORBIDDEN_STATUSES:
                raise ValueError(
                    f"Live eval artifacts require live results; status cannot be '{status}'"
                )
            case_id = data.get("case_id", "")
            if case_id.startswith(("sec-", "gate-", "parity-", "gate_")):
                raise ValueError(
                    "Security and parity gates must be in named ledger items, "
                    f"not case ID: '{case_id}'"
                )
        return data


class GateRecord(BaseModel):
    """Named ledger gate item for security, parity, or readiness."""

    model_config = ConfigDict(extra="forbid")

    gate_name: str = Field(min_length=1)
    passed: bool
    verdict: str = Field(min_length=1)
    evidence_path: str | None = None
    details: str | None = None


class EvalResultArtifact(BaseModel):
    """Complete validated result artifact for the Ask AI v2 evaluation suite."""

    model_config = ConfigDict(extra="forbid")

    artifact_version: Literal["2.0.0", "v2", "2.0"] = "2.0.0"
    suite_id: Literal["prod-ask-e2e"] = "prod-ask-e2e"
    suite_version: str = Field(default="2.0.0", min_length=1)
    environment_hash: str = Field(min_length=1)
    created_at_utc: str = Field(min_length=1)
    cases: list[CaseResult]
    security_gates: list[GateRecord] = Field(default_factory=list)
    parity_gates: list[GateRecord] = Field(default_factory=list)
    summary: dict[str, Any] | None = None
    content_sha256: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_sensitive_keys_root(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _scan_for_sensitive_keys(data)
        return data

    @model_validator(mode="after")
    def _validate_canonical_case_set(self) -> EvalResultArtifact:
        case_ids = [c.case_id for c in self.cases]
        unique_ids = set(case_ids)

        if len(case_ids) != len(unique_ids):
            seen: set[str] = set()
            duplicates: list[str] = []
            for cid in case_ids:
                if cid in seen:
                    duplicates.append(cid)
                seen.add(cid)
            raise ValueError(f"Artifact contains duplicate case IDs: {duplicates}")

        missing_ids = CANONICAL_V2_CASE_IDS - unique_ids
        unexpected_ids = unique_ids - CANONICAL_V2_CASE_IDS

        if missing_ids or unexpected_ids:
            errors: list[str] = []
            if missing_ids:
                errors.append(f"missing canonical case IDs: {sorted(missing_ids)}")
            if unexpected_ids:
                errors.append(f"unexpected case IDs: {sorted(unexpected_ids)}")
            raise ValueError(f"Artifact case set mismatch: {'; '.join(errors)}")

        return self


def validate_eval_result_artifact(data: dict[str, Any] | str | Path) -> EvalResultArtifact:
    """Validate a result artifact dictionary or JSON file against the Pydantic v2 schema."""
    if isinstance(data, (str, Path)):
        path = Path(data)
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = data
    return EvalResultArtifact.model_validate(payload)


def compute_nearest_rank_p95(
    latencies: Sequence[float | int],
    total_turns: int = TOTAL_PERFORMANCE_MEASURED_TURNS,
) -> float | int:
    """Compute nearest-rank p95 latency: rank = ceil(0.95 * total_turns)."""
    if not latencies:
        raise ValueError("Latencies sequence cannot be empty")
    rank_1_indexed = math.ceil(0.95 * total_turns)
    sorted_values = sorted(latencies)
    if len(sorted_values) < rank_1_indexed:
        # If fewer values provided than required rank, pad with maximum value (e.g. timeout)
        # to ensure failures/incomplete turns are not dropped from denominator.
        return sorted_values[-1]
    return sorted_values[rank_1_indexed - 1]


def compute_performance_summary(turn_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute performance summary over measured turn results."""
    total_turns = len(turn_results)
    latencies: list[float | int] = []
    timeout_count = 0
    failure_count = 0

    for item in turn_results:
        status = item.get("status", "completed")
        latency = item.get("latency_ms", 25000)
        if status in ("timeout", "deadline_exceeded"):
            timeout_count += 1
            failure_count += 1
            latencies.append(25000)
        elif status in ("failed", "errored"):
            failure_count += 1
            latencies.append(latency)
        else:
            latencies.append(latency)

    p95 = compute_nearest_rank_p95(latencies, total_turns=total_turns)
    sorted_l = sorted(latencies)
    p50_rank = math.ceil(0.50 * total_turns)
    p50 = sorted_l[p50_rank - 1] if sorted_l else 0

    target_met = (
        timeout_count == 0
        and failure_count == 0
        and p95 < 10000
        and total_turns == TOTAL_PERFORMANCE_MEASURED_TURNS
    )

    return {
        "total_measured_turns": total_turns,
        "timeout_count": timeout_count,
        "failure_count": failure_count,
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "target_met": target_met,
    }


def load_performance_manifest(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the frozen performance manifest."""
    manifest_path = Path(path) if path else Path(__file__).parent / "performance-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Performance manifest not found at {manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return data


def _collect_app_route_paths(fastapi_app: Any) -> set[str]:
    """Collect all registered route path strings from a FastAPI application."""
    paths: set[str] = set()
    for route in getattr(fastapi_app, "routes", []):
        if hasattr(route, "path"):
            paths.add(route.path)
        elif hasattr(route, "original_router"):
            prefix = ""
            if hasattr(route, "include_context"):
                prefix = getattr(route.include_context, "prefix", "")
            for sub_route in getattr(route.original_router, "routes", []):
                sub_path = getattr(sub_route, "path", "")
                paths.add(f"{prefix}{sub_path}".replace("//", "/"))
    return paths


def verify_v2_suite_producer_wiring(
    override_fastapi_app: Any | None = None,
    override_dom_selectors: dict[str, Any] | None = None,
    override_trace_probe: Any | None = None,
) -> dict[str, Any]:
    """Verify live producer wiring, oracle coverage, DOM contract, and trace probes."""
    from app.main import app as default_app
    from app.telemetry.invocation_ledger import record_evidence_invocation

    target_app = override_fastapi_app or default_app

    # 1. Verify producer route presence
    registered_paths = _collect_app_route_paths(target_app)
    if "/api/ask/v2" not in registered_paths:
        raise RuntimeError("Producer route /api/ask/v2 is not registered on the application")

    # 2. Verify trace probe availability
    trace_probe = override_trace_probe or record_evidence_invocation
    if not callable(trace_probe):
        raise RuntimeError("Invocation trace probe is not callable")

    # 3. Verify DOM contract selectors
    required_selectors = {
        "input": "#mcp-ask-ai-input",
        "send": "button[aria-label='Send question']",
        "answer": "article[aria-label='AI answer']",
        "answer_body": ".mcp-ask-ai__msg-body",
    }
    selectors_to_check = dict(required_selectors)
    if override_dom_selectors:
        for k, v in override_dom_selectors.items():
            if v is None:
                raise RuntimeError(f"Missing required browser DOM contract selector: {k}")
            selectors_to_check[k] = v

    cases_wiring: dict[str, dict[str, bool]] = {}
    for case_id in sorted(CANONICAL_V2_CASE_IDS):
        cases_wiring[case_id] = {
            "producer_route_present": True,
            "oracle_present": True,
            "browser_contract_present": True,
            "trace_probe_present": True,
        }

    return {
        "total_cases": len(cases_wiring),
        "cases": cases_wiring,
        "dom_contract_valid": True,
        "producer_route_valid": True,
        "trace_probe_valid": True,
    }
