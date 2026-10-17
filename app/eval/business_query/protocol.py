"""Protocol-smoke run kind: validity and latency metadata only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from app.eval.business_query.contract import RunKindError, assert_run_kind_legal

PROTOCOL_PREFLIGHT_FIELDS = (
    "route_id",
    "deployment",
    "provider",
    "wire_model",
    "structured_output_mode",
    "reasoning_effort",
    "request_timeout_s",
    "provider_retry_budget",
    "prompt_adjunct_hash",
    "schema_hash",
    "bundle_hash",
    "evaluator_hashes",
)

PROTOCOL_RESULT_FIELDS = (
    "protocol_valid",
    "latency_ms",
    "tokens_prompt",
    "tokens_completion",
)

_FORBIDDEN_PROTOCOL_KEYS = frozenset(
    {"accuracy", "ACCEPTED", "accepted", "case_pass", "score", "verdict"}
)


def assert_protocol_preflight(preflight: Mapping[str, Any]) -> None:
    missing = [field for field in PROTOCOL_PREFLIGHT_FIELDS if field not in preflight]
    if missing:
        raise RunKindError(f"protocol preflight missing {missing}")


def protocol_result(
    *,
    protocol_valid: bool,
    latency_ms: float | None,
    tokens_prompt: int | None,
    tokens_completion: int | None,
) -> dict[str, Any]:
    return {
        "protocol_valid": protocol_valid,
        "latency_ms": latency_ms,
        "tokens_prompt": tokens_prompt,
        "tokens_completion": tokens_completion,
    }


def assert_protocol_result(payload: Mapping[str, Any]) -> None:
    extra = _FORBIDDEN_PROTOCOL_KEYS.intersection(payload)
    if extra:
        raise RunKindError(f"protocol result cannot carry {sorted(extra)}")
    missing = [field for field in PROTOCOL_RESULT_FIELDS if field not in payload]
    if missing:
        raise RunKindError(f"protocol result missing {missing}")


def assert_protocol_run_kind(run_kind: Literal["protocol_smoke"]) -> None:
    assert_run_kind_legal(run_kind, subset=False, emit_release_verdict=False)
