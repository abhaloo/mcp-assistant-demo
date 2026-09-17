"""Run a frozen Business Query accuracy suite against the eval database.

Release evidence requires 80 independently approved cases, oracles, scoring
specs, and durable executor events. Smoke mode remains non-gating.

Spend is gated the same way as the canary — preflight resolves the model and
bundle and exits, and no paid call happens without --confirm-spend.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from app.business_query.definitions import current_bundle
from app.business_query.plan.planner_repair import _SEMANTIC_REPAIR_MAX, PLANNER_REPAIR_MAX
from app.eval.business_query import run_support as _run_support
from app.eval.business_query.contract import (
    FrozenFamilyManifest,
    RunKindError,
    assert_call_ceiling_matches,
    assert_run_kind_legal,
    call_ceiling,
    resolve_run_kind,
)
from app.eval.business_query.harness import SpendCeiling as SpendCeiling
from app.eval.business_query.harness import billing_engine, make_recorder
from app.eval.business_query.holdout import HoldoutAccessDenied, assert_holdout_path_unlocked
from app.eval.business_query.preflight import (
    EVAL_ADAPTER_STATEMENT_TIMEOUT_SECONDS as EVAL_ADAPTER_STATEMENT_TIMEOUT_SECONDS,
)
from app.eval.business_query.preflight import (
    run_eval_preflight as _preflight,
)
from app.eval.business_query.readiness import (
    EvaluationReadinessError,
    load_jsonl,
    load_scoring_specs,
    select_run_cases,
    validate_eval_readiness,
)
from app.eval.business_query.summary import build_summary
from app.telemetry.invocation_ledger import set_ledger_scope

build_eval_module = _run_support.build_eval_module
_evidence_context = _run_support.evidence_context
_execute_scored_repeat = _run_support.execute_scored_repeat
_run_one = _run_support.run_one
_run_paid_suite = _run_support.run_paid_suite
_write_results = _run_support.write_results

CASES_PATH = Path("evals/business_query/v2/cases.dev.jsonl")
ORACLE_PATH = Path("evals/business_query/v2/oracle-results.jsonl")
SCORING_SPECS_PATH = Path("evals/business_query/v2/scoring-specs.jsonl")
FAMILY_MANIFEST_PATH = Path("evals/business_query/v2/family-manifest.json")


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    set_ledger_scope("eval")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-name", default="mcp_bq_eval")
    ap.add_argument("--out", default="data/business-query-eval")
    ap.add_argument("--max-calls", type=int, default=None)
    ap.add_argument(
        "--repeats", type=int, default=1, help="runs per case; >1 exposes planner variance"
    )
    ap.add_argument(
        "--only", default="", help="comma-separated case ids; re-run failures, not the whole suite"
    )
    ap.add_argument("--cases", default=str(CASES_PATH))
    ap.add_argument("--oracle", default=str(ORACLE_PATH))
    ap.add_argument("--scoring-specs", default=str(SCORING_SPECS_PATH))
    ap.add_argument("--family-manifest", default=str(FAMILY_MANIFEST_PATH))
    ap.add_argument("--holdout-receipt")
    ap.add_argument(
        "--arm",
        choices=("module-control", "module-candidate"),
        default="module-control",
        help="causal pair arm; both mint E1 executor events. legacy arms are not accepted.",
    )
    ap.add_argument("--expected-route-id")
    ap.add_argument("--expected-deployment")
    ap.add_argument("--expected-provider")
    ap.add_argument("--expected-reasoning-effort")
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="run exactly one case and fail if the planner cannot produce a valid outcome",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="summary only. Running the SEALED HOLDOUT prints aggregates and nothing "
        "per-case, so looking at the score does not burn the holdout.",
    )
    ap.add_argument("--confirm-spend", action="store_true")
    ap.add_argument(
        "--run-kind",
        choices=("gate", "diagnostic"),
        default="gate",
        help="paid run kind when --confirm-spend is set; dry-run stays diagnostic",
    )
    args = ap.parse_args()

    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    if args.confirm_spend and not all(
        (
            args.expected_route_id,
            args.expected_deployment,
            args.expected_provider,
            args.expected_reasoning_effort,
        )
    ):
        raise SystemExit(
            "paid runs require --expected-route-id, --expected-deployment, "
            "--expected-provider, and --expected-reasoning-effort"
        )
    try:
        run_kind = resolve_run_kind(
            smoke=args.smoke,
            confirm_spend=args.confirm_spend,
            run_kind_flag=args.run_kind,
        )
        assert_run_kind_legal(run_kind, subset=bool(args.only), emit_release_verdict=False)
    except RunKindError as exc:
        raise SystemExit(str(exc)) from exc

    cases_path = Path(args.cases)
    oracle_path = Path(args.oracle)
    scoring_specs_path = Path(args.scoring_specs)
    try:
        from app.config import settings
        from app.eval.business_query.holdout import DEFAULT_TRUSTED_ARTIFACT_ROOT

        assert_holdout_path_unlocked(
            cases_path,
            receipt_path=Path(args.holdout_receipt) if args.holdout_receipt else None,
            signing_key=settings.redaction_hmac_key,
            trusted_artifact_root=DEFAULT_TRUSTED_ARTIFACT_ROOT,
        )
    except HoldoutAccessDenied as exc:
        raise SystemExit(f"PREFLIGHT FAIL: {exc}") from exc
    try:
        cases = load_jsonl(cases_path, "id")
        oracle = load_jsonl(oracle_path, "case_id")
        scoring_specs = load_scoring_specs(scoring_specs_path)
        manifest = FrozenFamilyManifest.load(Path(args.family_manifest))
    except FileNotFoundError as exc:
        raise SystemExit(f"PREFLIGHT FAIL: frozen eval input not found: {exc.filename}") from exc
    except (json.JSONDecodeError, KeyError, EvaluationReadinessError) as exc:
        raise SystemExit(f"PREFLIGHT FAIL: {exc}") from exc
    try:
        validate_eval_readiness(
            cases,
            oracle,
            scoring_specs,
            require_release_suite=run_kind == "gate",
            run_kind=run_kind,
            manifest=manifest,
        )
    except EvaluationReadinessError as exc:
        raise SystemExit(f"PREFLIGHT FAIL: {exc}") from exc
    excluded_case_ids = [cid for cid, row in cases.items() if row.get("eval_status") == "excluded"]
    only = {c.strip() for c in args.only.split(",") if c.strip()} if args.only else None
    cases = select_run_cases(cases, only=only)
    scoring_specs = {k: v for k, v in scoring_specs.items() if k in cases}
    if not cases:
        raise SystemExit("no cases selected")
    if args.smoke and (len(cases) != 1 or args.repeats != 1):
        raise SystemExit("--smoke requires exactly one selected case and --repeats 1")
    expected_calls = call_ceiling(
        case_count=len(cases),
        repeats=args.repeats,
        repair_budget=PLANNER_REPAIR_MAX + _SEMANTIC_REPAIR_MAX,
    )
    if args.max_calls is None:
        args.max_calls = expected_calls
    else:
        try:
            assert_call_ceiling_matches(
                args.max_calls,
                case_count=len(cases),
                repeats=args.repeats,
                repair_budget=PLANNER_REPAIR_MAX + _SEMANTIC_REPAIR_MAX,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    if args.max_calls < 1:
        raise SystemExit("--max-calls must be at least 1")
    engine = billing_engine(args.db_name)
    preflight = _preflight(
        engine,
        cases,
        oracle,
        expected_route_id=args.expected_route_id,
        expected_deployment=args.expected_deployment,
        expected_provider=args.expected_provider,
        expected_reasoning_effort=args.expected_reasoning_effort,
        db_name=args.db_name,
        cases_path=cases_path,
        oracle_path=oracle_path,
        scoring_specs_path=scoring_specs_path,
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = out_dir / "preflight.json"
    if preflight_path.exists():
        raise SystemExit(f"PREFLIGHT FAIL: output already exists: {preflight_path}")
    preflight["run_kind"] = run_kind
    preflight["release_gating"] = run_kind == "gate"
    preflight["causal_arm"] = args.arm
    preflight["excluded_case_ids"] = excluded_case_ids
    preflight["repeats"] = args.repeats
    preflight["max_calls"] = args.max_calls
    preflight["planner_repair_max"] = PLANNER_REPAIR_MAX
    preflight_path.write_text(
        json.dumps(preflight, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    if not args.confirm_spend:
        print("\nDRY RUN (no --confirm-spend): preflight only, zero paid calls made.")
        return 0

    from app.db.postgres import get_session_factory
    from app.eval.business_query.run_support import event_keyring as _event_keyring

    counter: dict[str, int] = {}
    recorder = make_recorder(counter, args.max_calls)
    bundle = current_bundle()
    results = asyncio.run(
        _run_paid_suite(
            cases=cases,
            oracle=oracle,
            scoring_specs=scoring_specs,
            engine=engine,
            bundle=bundle,
            recorder=recorder,
            preflight=preflight,
            run_id=f"bq-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S%fZ')}",
            event_keyring=_event_keyring(),
            session_factory=get_session_factory(),
            repeats=args.repeats,
            out_dir=out_dir,
            quiet=args.quiet,
        )
    )
    summary = build_summary(
        results,
        preflight=preflight,
        repeats=args.repeats,
        planner_calls=counter,
        cases=len({r["case_id"] for r in results}),
    )
    _write_results(out_dir, results)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    print(f"\nACCURACY: {summary['passed']}/{len(results)} ({summary['accuracy']:.0%})")
    print(f"outcome correct: {summary['outcome_correct']}/{len(results)}")
    print("by phrasing:", json.dumps(summary["by_phrasing"]))
    print("failures by layer:", json.dumps(summary["failures_by_layer"]))
    print("latency:", json.dumps(summary["latency"]))
    print("tokens:", json.dumps(summary["tokens"]))
    if args.smoke:
        smoke_ok = (
            len(results) == 1
            and results[0]["actual"] == "answered"
            and results[0]["failure_layer"] != "planner"
            and results[0]["receipt_valid"]
        )
        if not smoke_ok:
            print(
                "SMOKE FAILED: planner structured-output path did not produce "
                "a receipt-bearing answer"
            )
            return 2
        print("SMOKE PASSED: planner structured-output path produced a receipt-bearing answer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
