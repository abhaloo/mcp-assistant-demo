"""Cross-domain composition benchmark runner.

oracle   verify the recorded oracle hashes against oracle/*.sql
engine   run the authored plans through the real module with a scripted planner
diff     join two or more engine-arm jsonl files by id
model    production planner (spends; --confirm-spend; not run in this spike)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.eval.business_query.cross_domain import (  # noqa: E402
    BENCH_DIR,
    EngineArmResult,
    assert_oracle_sql_unchanged,
    column_agreement,
    format_diff,
    load_oracle_results,
    load_plans,
    run_engine_arm,
)

_ARM_TOKENS = frozenset({"cube", "internal", "chain"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("oracle", help="verify oracle hashes")
    engine = sub.add_parser("engine", help="engine arm: authored plans, no model call")
    engine.add_argument("--database", required=True)
    engine.add_argument("--adapter", choices=("chain", "cube", "internal"), default="chain")
    engine.add_argument("--api-readyz", default=None)
    engine.add_argument("--only", nargs="*", default=None)
    model = sub.add_parser("model", help="model arm (spends)")
    model.add_argument("--database", required=True)
    model.add_argument("--only", nargs="*", default=None)
    model.add_argument("--confirm-spend", action="store_true", default=False)
    diff = sub.add_parser("diff", help="join engine-arm jsonl files by id")
    diff.add_argument("runs", nargs="+")
    diff.add_argument("--write-agreement", type=Path, default=None)
    return parser


def format_table(results: list[EngineArmResult]) -> str:
    counts = Counter(item.classification for item in results)
    lines = [f"{item.id:6} {item.classification:15} {item.detail[:90]}" for item in results]
    summary = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
    return "\n".join([*lines, summary])


def _write_run(results: list[EngineArmResult], arm: str) -> Path:
    runs = REPO_ROOT / BENCH_DIR / "runs"
    runs.mkdir(exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    path = runs / f"{stamp}-{arm}.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in results:
            handle.write(
                json.dumps(
                    {
                        "id": item.id,
                        "classification": item.classification,
                        "detail": item.detail,
                        "total_row_count": item.total_row_count,
                        "adapters_used": item.adapters_used,
                        "cube_calls_ms": item.cube_calls_ms,
                        "latency_ms": item.latency_ms,
                        "rows": item.rows,
                        "preview_rows": item.rows[:5],
                    },
                    default=str,
                )
                + "\n"
            )
    return path


def _load_run(path: Path) -> list[EngineArmResult]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        raw = json.loads(line)
        rows.append(
            EngineArmResult(
                raw["id"],
                raw["classification"],
                raw.get("detail", ""),
                raw.get("rows", []),
                raw.get("total_row_count"),
                raw.get("adapters_used", []),
                raw.get("cube_calls_ms", []),
                raw.get("latency_ms", 0),
            )
        )
    return rows


def _arm_key(path: Path) -> str:
    token = path.stem.rsplit("-", 1)[-1]
    if token not in _ARM_TOKENS:
        raise SystemExit(f"diff run name must end in cube|internal|chain: {path}")
    return token


_TIME_VALUE_KINDS = frozenset({"date", "datetime"})
_MIN_CASES_FOR_TIME_SHAPE = 2


def _member_is_time(bundle: object, member: str) -> bool:
    from app.business_query.definitions import resolve_member

    resolved = resolve_member(bundle, member)
    definition = None
    if resolved is not None:
        _kind, definition = resolved
    if definition is None:
        definition = next(
            (item for item in bundle.dimensions if item.name == member),
            None,
        )
    if definition is None:
        definition = next((item for item in bundle.measures if item.name == member), None)
    if definition is None:
        return False
    if getattr(definition, "type", None) == "time":
        return True
    return getattr(definition, "value_kind", None) in _TIME_VALUE_KINDS


def _requires_time_evidence(plan: object, bundle: object) -> bool:
    if getattr(plan, "period", None) is not None:
        return True
    if any(_member_is_time(bundle, name) for name in plan.dimensions):
        return True
    return any(_member_is_time(bundle, clause.member) for clause in plan.order)


def _compared_columns(expected_rows: list[dict], actual_rows: list[dict]) -> list[str]:
    keys: set[str] = set()
    for row in (*expected_rows, *actual_rows):
        keys.update(row)
    return sorted(keys)


def write_agreement_artifact(
    runs: dict[str, list[EngineArmResult]],
    path: Path,
    *,
    plans: dict,
    bundle: object,
) -> None:
    """Write one shape record per plan_shape_key seen on both cube and internal."""
    from app.business_query.plan.plan_shape import plan_shape_key
    from app.business_query.plan.query_plan import BusinessQueryPlan

    by_id: dict[str, dict[str, EngineArmResult]] = {}
    for name, rows in runs.items():
        for row in rows:
            by_id.setdefault(row.id, {})[name] = row
    grouped: dict[str, list[dict]] = {}
    for case_id, arms in by_id.items():
        cube = arms.get("cube")
        internal = arms.get("internal")
        if (
            cube is None
            or internal is None
            or cube.classification != "pass"
            or internal.classification != "pass"
        ):
            continue
        authored = plans.get(case_id)
        if authored is None or authored.plan is None:
            continue
        plan = BusinessQueryPlan.model_validate(authored.plan)
        key = plan_shape_key(plan)
        columns = column_agreement(
            internal.rows,
            cube.rows,
            _compared_columns(internal.rows, cube.rows),
        )
        grouped.setdefault(key, []).append(
            {
                "columns": columns,
                "requires_time_evidence": _requires_time_evidence(plan, bundle),
            }
        )
    shapes = []
    for key, cases in sorted(grouped.items()):
        column_names = sorted({name for case in cases for name in case["columns"]})
        columns = {
            name: all(case["columns"].get(name, False) for case in cases) for name in column_names
        }
        requires_time_evidence = any(case["requires_time_evidence"] for case in cases)
        agreed = bool(columns) and all(columns.values())
        if requires_time_evidence and len(cases) < _MIN_CASES_FOR_TIME_SHAPE:
            agreed = False
        time_columns_agreed = sum(
            ok and _member_is_time(bundle, name) for name, ok in columns.items()
        )
        shapes.append(
            {
                "key": key,
                "agreed": agreed,
                "requires_time_evidence": requires_time_evidence,
                "time_columns_agreed": time_columns_agreed,
                "columns": columns,
            }
        )
    path.write_text(json.dumps({"shapes": shapes}) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bench_dir = REPO_ROOT / BENCH_DIR
    if args.command == "oracle":
        assert_oracle_sql_unchanged(bench_dir, load_oracle_results(bench_dir))
        print("oracle hashes match")
        return 0
    if args.command == "diff":
        runs = {_arm_key(Path(p)): _load_run(Path(p)) for p in args.runs}
        print(format_diff(runs))
        if args.write_agreement is not None:
            from app.business_query.definitions import current_bundle

            write_agreement_artifact(
                runs,
                args.write_agreement,
                plans=load_plans(bench_dir),
                bundle=current_bundle(),
            )
        return 0
    from app.business_query.definitions import current_bundle
    from app.eval.business_query.harness import billing_engine

    engine = billing_engine(args.database)
    only = set(args.only) if args.only else None
    if args.command == "engine":
        results = asyncio.run(
            run_engine_arm(
                engine=engine,
                bundle=current_bundle(),
                bench_dir=bench_dir,
                only=only,
                adapter=args.adapter,
                api_readyz=args.api_readyz,
            )
        )
        print(format_table(results))
        print(f"written: {_write_run(results, args.adapter)}")
        return 0
    if not args.confirm_spend:
        print(
            "the model arm spends; pass --confirm-spend after /adversarial-review",
            file=sys.stderr,
        )
        return 2
    print("model arm is not part of this spike", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
