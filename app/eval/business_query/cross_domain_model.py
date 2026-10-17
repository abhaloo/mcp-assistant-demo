"""Model arm of the cross-domain benchmark: the production planner plans each question.

Spends model calls. The CLI gates it behind --confirm-spend; this module never
decides to run on its own."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.engine import Engine

from app.business_query.composition import ModulePlugins, build_module
from app.business_query.definitions import DefinitionBundle
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace
from app.core.turn_budget import UNBOUNDED_BUDGET
from app.eval.business_query.cross_domain import (
    ALL_PERMISSIONS,
    BUSINESS_DATE,
    Classification,
    EngineArmResult,
    Expected,
    assert_oracle_sql_unchanged,
    load_cases,
    load_oracle_results,
    load_scoring_specs,
    merge_companion_rows,
    values_match,
)
from app.eval.business_query.harness import eval_principal

ModelArmClassification = Literal[
    "planned_match",
    "planned_wrong",
    "planned_invalid",
    "honest_unsupported",
    "clarify",
    "stall",
    "capability_gap",
]


def classify_model_outcome(
    *, expected: Expected, outcome_kind: str, matched: bool | None
) -> tuple[Classification, ModelArmClassification]:
    if outcome_kind == "answered":
        if expected == "capability_gap":
            return "capability_gap", "planned_match" if matched else "planned_wrong"
        return ("pass", "planned_match") if matched else ("wrong_answer", "planned_wrong")
    if outcome_kind == "unsupported":
        return (
            "capability_gap" if expected == "capability_gap" else "engine_rule"
        ), "honest_unsupported"
    if outcome_kind == "clarify":
        return ("capability_gap" if expected == "capability_gap" else "engine_rule"), "clarify"
    return ("capability_gap" if expected == "capability_gap" else "no_plan"), "stall"


async def run_model_arm(
    *,
    engine: Engine,
    bundle: DefinitionBundle,
    bench_dir: Path,
    only: set[str] | None = None,
) -> list[EngineArmResult]:
    cases = load_cases(bench_dir)
    oracles = load_oracle_results(bench_dir)
    specs = load_scoring_specs(bench_dir)
    assert_oracle_sql_unchanged(bench_dir, oracles)
    executor = ThreadPoolExecutor(max_workers=2)
    principal = eval_principal({"principal_override": {"permissions": list(ALL_PERMISSIONS)}})
    results: list[EngineArmResult] = []
    for case in cases:
        if only and case.id not in only:
            continue
        trace = QueryTrace()
        module = build_module(
            principal=principal,
            engine=engine,
            executor=executor,
            bundle=bundle,
            database_identity="bench:cross_domain:model",
            plugins=ModulePlugins(trace=trace),
        )
        request = BusinessQueryRequest(
            question=case.question,
            principal=principal,
            correlation_id=f"xd-model-{case.id}",
            business_date=BUSINESS_DATE,
            max_rows=50,
        )
        outcome = await module.query(request, turn_budget=UNBOUNDED_BUDGET)
        kind = outcome.outcome
        matched: bool | None = None
        rows: list[dict[str, Any]] = []
        total: int | None = None
        detail = f"{kind}: {getattr(outcome, 'reason_code', '')}".strip()
        if kind == "answered":
            rows = [dict(row) for row in outcome.rows]
            total = outcome.total_row_count
            for companion in outcome.companion_answered:
                rows = merge_companion_rows(rows, [dict(row) for row in companion.rows])
                total = len(rows)
            matched, detail = values_match(rows, total, oracles[case.id], specs.get(case.id))
        shared, fine = classify_model_outcome(
            expected=case.expected, outcome_kind=kind, matched=matched
        )
        results.append(
            EngineArmResult(case.id, shared, f"{fine} {detail}".strip()[:300], rows, total)
        )
    return results
