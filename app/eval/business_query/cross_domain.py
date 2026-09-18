"""Cross-domain composition benchmark: cases, authored plans, oracle results, engine arm.

The engine arm runs each authored plan through the real business-query module with a
scripted planner (no model call) against a snapshot database, compares the answer to an
independently written SQL oracle, and classifies the result."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import Engine

from app.business_query.compile.adapter import InternalCompilerAdapter
from app.business_query.compile.cube_adapter import _unsupported_plan_reason
from app.business_query.composition import ModulePlugins, build_cube_adapter, build_module
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import AdapterUnsupported, Incomplete
from app.business_query.plan import PlanFilter, iter_filter_leaves
from app.business_query.plan.planned_set import PlannedQuerySet
from app.business_query.plan.query_plan import BusinessQueryPlan
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace
from app.core.turn_budget import UNBOUNDED_BUDGET
from app.eval.business_query.harness import eval_principal

BENCH_DIR = Path("evals/business_query/cross_domain")
BUSINESS_DATE = date(2026, 7, 15)
ALL_PERMISSIONS: tuple[str, ...] = (
    "view customer",
    "view customer order",
    "view inventory",
    "view invoice",
    "view job",
    "view journal entries",
    "view payable quotation",
    "view quotation",
    "view supplier",
)
Expected = Literal["answer", "capability_gap"]
Classification = Literal[
    "pass", "wrong_answer", "engine_rule", "invalid_plan", "capability_gap", "no_plan"
]
_NUMERIC_TOLERANCE = Decimal("0.01")
_SET_OPS = frozenset({"in_set", "not_in_set"})


class BenchCase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", populate_by_name=True)
    id: str
    question: str
    class_: str = Field(alias="class")
    expected: Expected
    oracle_sql: str
    gap_reason: str | None = None
    engine_today: str | None = None
    design_operator: str | None = None


class AuthoredPlan(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    plan: dict[str, Any] | None = None
    companions: list[dict[str, Any]] = Field(default_factory=list)
    alt: dict[str, Any] | None = None
    gap: str | None = None


class OracleResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    sql_sha256: str
    database: str
    business_date: str
    columns: list[str]
    row_count: int
    rows: list[list[Any]]


class ScoringSpec(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    compare_columns: list[str]
    note: str | None = None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_cases(bench_dir: Path) -> list[BenchCase]:
    return [BenchCase.model_validate(row) for row in _read_jsonl(bench_dir / "cases.jsonl")]


def load_plans(bench_dir: Path) -> dict[str, AuthoredPlan]:
    plans = [AuthoredPlan.model_validate(row) for row in _read_jsonl(bench_dir / "plans.jsonl")]
    return {item.id: item for item in plans}


def load_oracle_results(bench_dir: Path) -> dict[str, OracleResult]:
    rows = _read_jsonl(bench_dir / "oracle-results.jsonl")
    return {item.id: item for item in (OracleResult.model_validate(row) for row in rows)}


def load_scoring_specs(bench_dir: Path) -> dict[str, ScoringSpec]:
    path = bench_dir / "scoring-specs.jsonl"
    if not path.exists():
        return {}
    return {item.id: item for item in (ScoringSpec.model_validate(r) for r in _read_jsonl(path))}


def assert_oracle_sql_unchanged(bench_dir: Path, results: dict[str, OracleResult]) -> None:
    for case_id, result in results.items():
        sql = (bench_dir / "oracle" / f"{case_id}.sql").read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if digest != result.sql_sha256:
            raise ValueError(f"oracle SQL for {case_id} changed since its result was recorded")


def _text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    return str(value).strip().replace(" ", "T").casefold()


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right if isinstance(left, bool) and isinstance(right, bool) else False
    if isinstance(left, (int, float, Decimal)) and isinstance(right, (int, float, Decimal)):
        return abs(Decimal(str(left)) - Decimal(str(right))) <= _NUMERIC_TOLERANCE
    if left is None or right is None:
        return left is right
    return _text(left) == _text(right)


def _row_in(needle: list[Any], haystack: list[list[Any]]) -> bool:
    return any(
        all(any(_same_value(cell, value) for cell in row) for value in needle) for row in haystack
    )


def rows_agree(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    """Same rows in any order, compared value by value like the oracle match.

    Two adapters may return the same answer with different row order or with a
    number rendered as text; both still agree on values.
    """
    if len(left) != len(right):
        return False
    unused = list(right)
    for row in left:
        match = next(
            (
                candidate
                for candidate in unused
                if candidate.keys() == row.keys()
                and all(_same_value(row[key], candidate[key]) for key in row)
            ),
            None,
        )
        if match is None:
            return False
        unused.remove(match)
    return True


def values_match(
    rows: list[dict[str, Any]],
    total_row_count: int,
    oracle: OracleResult,
    spec: ScoringSpec | None = None,
) -> tuple[bool, str]:
    if total_row_count != oracle.row_count:
        return False, f"row_count {total_row_count} != oracle {oracle.row_count}"
    keep = [oracle.columns.index(name) for name in spec.compare_columns] if spec else None
    oracle_rows = [[row[i] for i in keep] if keep else list(row) for row in oracle.rows]
    answer_rows = [list(row.values()) for row in rows]
    if len(rows) < total_row_count:
        # A NULL cell in a truncated answer may be a column the spec never
        # compares, so only its non-NULL cells are checked against the oracle.
        for answer in answer_rows:
            present = [v for v in answer if v is not None]
            if not _row_in(present, oracle_rows):
                return False, f"answer row {present} not found in the oracle"
        return True, ""
    for expected in oracle_rows:
        if not _row_in(expected, answer_rows):
            return False, f"oracle row {expected} not found in {len(rows)} answer row(s)"
    return True, ""


def merge_companion_rows(
    rows: list[dict[str, Any]], extra: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(rows) == 1 and len(extra) == 1:
        return [{**rows[0], **extra[0]}]
    merged: list[dict[str, Any]] = []
    unused = list(extra)
    for row in rows:
        labels = {_text(v) for v in row.values() if isinstance(v, str)}
        match = next(
            (e for e in unused if labels & {_text(v) for v in e.values() if isinstance(v, str)}),
            None,
        )
        if match is None:
            return rows + extra
        unused.remove(match)
        merged.append({**row, **match})
    return merged + unused


def classify_outcome(
    *,
    expected: Expected,
    plan_error: str | None,
    outcome_kind: str | None,
    matched: bool | None,
) -> Classification:
    if plan_error is not None:
        return "invalid_plan"
    if outcome_kind is None:
        return "no_plan"
    if outcome_kind == "answered":
        return "pass" if matched else "wrong_answer"
    if expected == "capability_gap":
        return "capability_gap"
    return "engine_rule"


@dataclass(frozen=True, slots=True)
class EngineArmResult:
    id: str
    classification: Classification
    detail: str
    rows: list[dict[str, Any]]
    total_row_count: int | None
    adapters_used: list[str] = field(default_factory=list)
    cube_calls_ms: list[int] = field(default_factory=list)
    latency_ms: int = 0
    cube_incomplete: bool = False


@dataclass(frozen=True, slots=True)
class AdapterCall:
    name: str
    answered: bool
    elapsed_ms: int
    incomplete: bool = False


class _RecordingAdapter:
    """Wraps an execution adapter; the chain's isinstance check needs both methods."""

    def __init__(self, inner: Any, name: str, log: list[AdapterCall]) -> None:
        self._inner = inner
        self._name = name
        self._log = log

    def execute(self, scoped, *, trace=None):
        return self._timed(lambda: self._inner.execute(scoped, trace=trace))

    def execute_with_evidence(self, scoped, *, trace=None):
        return self._timed(lambda: self._inner.execute_with_evidence(scoped, trace=trace))

    def _timed(self, call):
        started = time.perf_counter()
        try:
            result = call()
        except AdapterUnsupported:
            self._log.append(
                AdapterCall(self._name, False, int((time.perf_counter() - started) * 1000))
            )
            raise
        incomplete = isinstance(result, Incomplete)
        self._log.append(
            AdapterCall(
                self._name,
                not incomplete,
                int((time.perf_counter() - started) * 1000),
                incomplete,
            )
        )
        return result


def _plan_uses_set_operator(plan: BusinessQueryPlan) -> bool:
    for group in (plan.filters, plan.having):
        if group is None:
            continue
        for leaf in iter_filter_leaves(group):
            if isinstance(leaf, PlanFilter) and leaf.operator in _SET_OPS:
                return True
    return False


def cube_would_refuse(plan: BusinessQueryPlan, bundle: DefinitionBundle) -> bool:
    """Same refusals the live Cube adapter raises as AdapterUnsupported, plus in_set."""
    if _unsupported_plan_reason(plan, bundle) is not None:
        return True
    return _plan_uses_set_operator(plan)


def unvalidatable_primary_ids(plans: dict[str, AuthoredPlan]) -> set[str]:
    """Case ids whose primary blob is not a BusinessQueryPlan."""
    bad: set[str] = set()
    for case_id, authored in plans.items():
        if authored.plan is None:
            continue
        try:
            BusinessQueryPlan.model_validate(authored.plan)
        except ValidationError:
            bad.add(case_id)
    return bad


def expected_chain_cube_calls(plans: dict[str, AuthoredPlan], bundle: DefinitionBundle) -> int:
    """Count primary + companion plans Cube would answer. Never count alt.

    Invalid blobs raise; callers must exclude ids from unvalidatable_primary_ids.
    """
    total = 0
    for authored in plans.values():
        blobs = []
        if authored.plan is not None:
            blobs.append(authored.plan)
        blobs.extend(authored.companions)
        for blob in blobs:
            plan = BusinessQueryPlan.model_validate(blob)
            if not cube_would_refuse(plan, bundle):
                total += 1
    return total


def _abort_unless_cube_ready(api_readyz: str) -> None:
    try:
        response = httpx.get(api_readyz, timeout=5.0)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError:
        print("cube not ready; aborting cube/chain arm", file=sys.stderr)  # noqa: T201
        raise SystemExit(2) from None
    if body.get("checks", {}).get("cube") is not True:
        print("cube not ready; aborting cube/chain arm", file=sys.stderr)  # noqa: T201
        raise SystemExit(2)


def _abort_if_cube_transport_failed(
    adapter: Literal["chain", "cube", "internal"], result: EngineArmResult
) -> None:
    if adapter not in {"cube", "chain"}:
        return
    if not result.cube_incomplete:
        return
    print("cube adapter invalid; aborting cube/chain arm", file=sys.stderr)  # noqa: T201
    raise SystemExit(2)


def append_alt_row(results: list[EngineArmResult], alt: EngineArmResult) -> list[EngineArmResult]:
    tagged = EngineArmResult(
        id=f"{alt.id} (alt)",
        classification=alt.classification,
        detail=alt.detail,
        rows=alt.rows,
        total_row_count=alt.total_row_count,
        adapters_used=alt.adapters_used,
        cube_calls_ms=alt.cube_calls_ms,
        latency_ms=alt.latency_ms,
        cube_incomplete=alt.cube_incomplete,
    )
    return [*results, tagged]


def _select_adapters(  # noqa: PLR0913
    *,
    adapter: Literal["chain", "cube", "internal"],
    principal,
    bundle,
    engine,
    database_identity: str,
    statement_timeout_seconds: float,
    trace: QueryTrace,
    log: list[AdapterCall],
) -> list[Any]:
    internal = InternalCompilerAdapter(
        principal,
        engine,
        bundle,
        statement_timeout_seconds=statement_timeout_seconds,
        trace=trace,
        database_identity=database_identity,
    )
    if adapter == "internal":
        return [_RecordingAdapter(internal, "internal", log)]
    cube = build_cube_adapter(
        principal=principal, bundle=bundle, database_identity=database_identity
    )
    wrapped_cube = _RecordingAdapter(cube, "cube", log)
    if adapter == "cube":
        return [wrapped_cube]
    return [wrapped_cube, _RecordingAdapter(internal, "internal", log)]


class AuthoredPlanner:
    """Returns one authored plan instead of asking a model."""

    def __init__(self, planned: PlannedQuerySet | BusinessQueryPlan) -> None:
        self._planned = planned

    async def plan(  # noqa: PLR0913
        self,
        question: str,
        card: str,
        *,
        business_date: date | None = None,
        retry_hint: str | None = None,
        trace: QueryTrace | None = None,
        clarification_exchange: tuple[str, str] | None = None,
        dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None = None,
        progress: Any = None,
    ) -> PlannedQuerySet | BusinessQueryPlan:
        return self._planned


def _to_planned(authored: AuthoredPlan) -> PlannedQuerySet | BusinessQueryPlan | None:
    if authored.plan is None:
        return None
    primary = BusinessQueryPlan.model_validate(authored.plan)
    companions = tuple(BusinessQueryPlan.model_validate(item) for item in authored.companions)
    return PlannedQuerySet(primary=primary, companions=companions) if companions else primary


async def run_engine_arm(  # noqa: PLR0913
    *,
    engine: Engine,
    bundle: DefinitionBundle,
    bench_dir: Path,
    only: set[str] | None = None,
    adapter: Literal["chain", "cube", "internal"] = "chain",
    api_readyz: str | None = None,
) -> list[EngineArmResult]:
    if adapter in {"cube", "chain"}:
        if not api_readyz:
            raise SystemExit(2)
        _abort_unless_cube_ready(api_readyz)
    cases = load_cases(bench_dir)
    plans = load_plans(bench_dir)
    oracles = load_oracle_results(bench_dir)
    specs = load_scoring_specs(bench_dir)
    assert_oracle_sql_unchanged(bench_dir, oracles)
    results: list[EngineArmResult] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        for case in cases:
            if only and case.id not in only:
                continue
            authored = plans[case.id]
            result = await _run_case(
                case,
                authored,
                oracles[case.id],
                specs.get(case.id),
                engine,
                bundle,
                executor,
                adapter,
            )
            results.append(result)
            _abort_if_cube_transport_failed(adapter, result)
            if authored.alt and authored.alt.get("plan"):
                alt = AuthoredPlan.model_validate({"id": case.id, **authored.alt})
                alt_result = await _run_case(
                    case,
                    alt,
                    oracles[case.id],
                    specs.get(case.id),
                    engine,
                    bundle,
                    executor,
                    adapter,
                )
                results = append_alt_row(results, alt_result)
                _abort_if_cube_transport_failed(adapter, results[-1])
    return results


def _result(  # noqa: PLR0913
    case_id: str,
    classification: Classification,
    detail: str,
    rows: list[dict[str, Any]],
    total: int | None,
    log: list[AdapterCall],
    latency_ms: int,
) -> EngineArmResult:
    return EngineArmResult(
        case_id,
        classification,
        detail[:300],
        rows,
        total,
        [c.name for c in log if c.answered],
        [c.elapsed_ms for c in log if c.name == "cube" and c.answered],
        latency_ms,
        cube_incomplete=any(c.name == "cube" and c.incomplete for c in log),
    )


async def _run_case(  # noqa: PLR0913
    case: BenchCase,
    authored: AuthoredPlan,
    oracle: OracleResult,
    spec: ScoringSpec | None,
    engine: Engine,
    bundle: DefinitionBundle,
    executor: ThreadPoolExecutor,
    adapter: Literal["chain", "cube", "internal"],
) -> EngineArmResult:
    log: list[AdapterCall] = []
    try:
        planned = _to_planned(authored)
    except ValueError as exc:
        first = str(exc).splitlines()
        detail = next(
            (line for line in first if "rule" in line or "error" in line.lower()), first[0]
        )
        return _result(
            case.id,
            classify_outcome(
                expected=case.expected, plan_error=detail, outcome_kind=None, matched=None
            ),
            detail,
            [],
            None,
            log,
            0,
        )
    if planned is None:
        return _result(
            case.id,
            classify_outcome(
                expected=case.expected, plan_error=None, outcome_kind=None, matched=None
            ),
            authored.gap or "no authored plan",
            [],
            None,
            log,
            0,
        )
    principal = eval_principal({"principal_override": {"permissions": list(ALL_PERMISSIONS)}})
    adapters = _select_adapters(
        adapter=adapter,
        principal=principal,
        bundle=bundle,
        engine=engine,
        database_identity="bench:cross_domain",
        statement_timeout_seconds=10.0,
        trace=QueryTrace(),
        log=log,
    )
    module = build_module(
        principal=principal,
        engine=engine,
        executor=executor,
        bundle=bundle,
        database_identity="bench:cross_domain",
        plugins=ModulePlugins(
            trace=QueryTrace(),
            planner=AuthoredPlanner(planned),
            adapters_override=adapters,
        ),
    )
    request = BusinessQueryRequest(
        question=f"[{case.id}] {case.question}",
        principal=principal,
        correlation_id=f"xd-bench-{case.id}",
        business_date=BUSINESS_DATE,
        max_rows=50,
    )
    started = time.perf_counter()
    outcome = await module.query(request, turn_budget=UNBOUNDED_BUDGET)
    latency_ms = int((time.perf_counter() - started) * 1000)
    kind = outcome.outcome
    if kind != "answered":
        detail = (
            f"{kind}: {getattr(outcome, 'reason_code', '')} {getattr(outcome, 'message', '')}"
        ).strip()
        return _result(
            case.id,
            classify_outcome(
                expected=case.expected, plan_error=None, outcome_kind=kind, matched=None
            ),
            detail,
            [],
            None,
            log,
            latency_ms,
        )
    rows = [dict(row) for row in outcome.rows]
    total = outcome.total_row_count
    for companion in outcome.companion_answered:
        rows = merge_companion_rows(rows, [dict(row) for row in companion.rows])
        total = len(rows)
    matched, detail = values_match(rows, total, oracle, spec)
    return _result(
        case.id,
        classify_outcome(
            expected=case.expected, plan_error=None, outcome_kind=kind, matched=matched
        ),
        detail,
        rows,
        total,
        log,
        latency_ms,
    )


def format_diff(runs: dict[str, list[EngineArmResult]]) -> str:
    by_id: dict[str, dict[str, EngineArmResult]] = {}
    for name, rows in runs.items():
        for row in rows:
            by_id.setdefault(row.id, {})[name] = row
    lines = []
    both: list[str] = []
    for case_id, arms in sorted(by_id.items()):
        cube = arms.get("cube")
        internal = arms.get("internal")
        agree = ""
        if (
            cube is not None
            and internal is not None
            and cube.classification == "pass"
            and internal.classification == "pass"
        ):
            agree = "agree" if rows_agree(cube.rows, internal.rows) else "disagree"
            if agree == "agree":
                both.append(case_id)
        used = " ".join(f"{k}={v.adapters_used}" for k, v in arms.items())
        classes = " ".join(f"{k}={v.classification}" for k, v in arms.items())
        lines.append(f"{case_id:12} {used} {classes} {agree}".rstrip())
    lines.append(f"g6_both_answered={len(both)} ids={both}")
    return "\n".join(lines)
