"""Summary statistics helpers for coordinator eval runs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.eval.conversation_coordinator import CaseResult, CoordinatorRunOutput


@dataclass(frozen=True)
class TraceContext:
    build_hash: str
    cases_hash: str
    suite_hash: str
    reason: str | None = None


def percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = int(len(ordered) * fraction)
    index = min(max(index, 0), len(ordered) - 1)
    return ordered[index]


def median(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def count_violations(results: Sequence[CaseResult]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        for name in result.invariant_violations:
            counts[name] = counts.get(name, 0) + 1
    return counts


def compare_baseline(frozen: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_p50 = frozen.get("p50_latency_ms")
    candidate_p50 = candidate.get("p50_latency_ms")
    return {
        "baseline_build_hash": frozen.get("build_hash"),
        "candidate_build_hash": candidate.get("build_hash"),
        "baseline_route_id": frozen.get("route_id"),
        "candidate_route_id": candidate.get("route_id"),
        "baseline_suite_hash": frozen.get("suite_hash"),
        "candidate_cases_hash": candidate.get("cases_hash"),
        "hashes_present": bool(frozen.get("build_hash") and candidate.get("build_hash")),
        "timings_comparable": baseline_p50 is not None and candidate_p50 is not None,
    }


def build_trace_row(
    case_res: CaseResult,
    run_output: CoordinatorRunOutput,
    ctx: TraceContext,
) -> dict[str, Any]:
    return {
        **case_res.model_dump(),
        "actions": list(run_output.actions),
        "answer_text": run_output.answer_text,
        "time_to_first_action_ms": run_output.time_to_first_action_ms,
        "route_id": run_output.route_id,
        "model": run_output.model,
        "run_ids": list(run_output.run_ids),
        "thread_id": run_output.thread_id,
        "reason": ctx.reason if ctx.reason is not None else run_output.reason,
        "build_hash": ctx.build_hash,
        "cases_hash": ctx.cases_hash,
        "suite_hash": ctx.suite_hash,
    }
