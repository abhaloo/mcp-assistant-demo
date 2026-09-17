"""Run the production-ask quality suite over POST /api/ask.

Three run modes, deliberately separated by what they cost and what they prove:

    stub    the real route with a scripted model and a fixture store. Zero
            spend. Safe on every change. Proves wiring, access boundaries, and
            transport parity, never answer correctness.
    replay  score answers recorded by an earlier live run. Zero spend. Proves
            nothing new about the route, and everything about the scorer.
    live    the real route against the configured models. Costs money and
            needs --confirm-spend.

The judged dimensions are a second, separate spend decision: --judge also
needs --confirm-spend, and a judge run is void unless its canary holds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# This worktree's app package must win over any editable install pointing at
# another checkout, or a run scores code it is not measuring.

# Evaluation must not export prompts or answers to a paid tracing service.
# Set before importing anything that reads the environment at import time.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

from fastapi.testclient import TestClient

from app.config import settings
from app.eval.ask_route.case import AskObservation, ProdAskCase, load_cases, load_sql_oracle
from app.eval.ask_route.scorer import (
    ProdAskCaseScore,
    add_permission_variance,
    score_observation,
    summarize,
    transport_parity,
    write_failures,
)
from app.eval.business_query.artifacts import seal_summary
from app.policy.manifest_loader import load_manifest

# The scripted-model and live drivers live beside their primary consumer, the
# ask-route suite. This script is dev-only and never runs inside the production
# image, where tests/ is absent.
from tests.harness.prod_ask_live_driver import run_live_json, run_live_sse
from tests.harness.prod_ask_stub_driver import run_stub_json, run_stub_sse

CASES_PATH = Path("evals/prod_ask/cases.jsonl")
ORACLE_PATH = Path("evals/prod_ask/oracle-results.jsonl")
CORPUS_ROOT = Path("data/corpus/company")
DEFAULT_OUT = Path("data/prod-ask-eval")
FAILURE_DIR = Path("evals/failures")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_tree(root: Path) -> str:
    """One hash over every corpus file, so a changed document invalidates gold."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _repo_identity(*, require_clean: bool) -> dict[str, object]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if dirty and require_clean:
        raise SystemExit("PREFLIGHT FAIL: tracked worktree is dirty; commit the eval inputs first")
    return {"head": head, "dirty": bool(dirty)}


def _preflight(args: argparse.Namespace, cases: dict[str, ProdAskCase]) -> dict[str, object]:
    """Everything a reader needs to tell two runs apart, resolved before spend."""
    paid = args.mode == "live" or args.judge
    info: dict[str, object] = {
        "suite": "prod_ask",
        "mode": args.mode,
        "transport": args.transport,
        "judge": bool(args.judge),
        "confirm_spend": bool(args.confirm_spend),
        "recorded_at": datetime.now(tz=UTC).isoformat(),
        "repo": _repo_identity(require_clean=paid),
        "cases_path": str(args.cases),
        "cases_sha256": _sha256_file(Path(args.cases)),
        "case_count": len(cases),
        "case_ids": sorted(cases),
        "oracle_path": str(args.oracle),
        "oracle_sha256": _sha256_file(Path(args.oracle)) if Path(args.oracle).exists() else None,
        "corpus_sha256": _sha256_tree(CORPUS_ROOT) if CORPUS_ROOT.exists() else None,
        "policy_manifest_hash": None,
        "settings": {
            "active_chat_model": settings.active_chat_model,
            "chat_provider": settings.chat_provider,
            "top_k": settings.top_k,
            "record_analytics_mode": settings.record_analytics_mode,
            "business_query_mode": settings.business_query_mode,
            "citation_completion_gate": settings.citation_completion_gate,
            "langsmith_tracing": settings.langsmith_tracing,
        },
    }
    try:
        manifest = load_manifest()
        info["policy_manifest_hash"] = getattr(manifest, "manifest_hash", None) or getattr(
            manifest, "content_hash", None
        )
        info["manifest_resources"] = sorted(manifest.resources)
    except Exception as exc:  # noqa: BLE001 - preflight reports, it does not decide
        info["manifest_error"] = str(exc)
    if settings.langsmith_tracing:
        raise SystemExit("PREFLIGHT FAIL: LANGSMITH_TRACING must be false for an eval run")
    return info


def _selected(cases: dict[str, ProdAskCase], args: argparse.Namespace) -> dict[str, ProdAskCase]:
    only = {c.strip() for c in args.only.split(",") if c.strip()} if args.only else None
    chosen = {
        cid: case
        for cid, case in cases.items()
        if args.mode in case.modes and (only is None or cid in only)
    }
    if only:
        unknown = sorted(only - set(cases))
        if unknown:
            raise SystemExit(f"PREFLIGHT FAIL: unknown case ids {unknown}")
    if not chosen:
        raise SystemExit(f"no cases support mode {args.mode}")
    return chosen


def _observe(
    client: TestClient, case: ProdAskCase, mode: str, transports: list[str]
) -> list[AskObservation]:
    runners = {
        ("stub", "json"): run_stub_json,
        ("stub", "sse"): run_stub_sse,
        ("live", "json"): run_live_json,
        ("live", "sse"): run_live_sse,
    }
    return [runners[(mode, transport)](client, case) for transport in transports]


def _load_recorded(path: Path) -> list[AskObservation]:
    return [
        AskObservation.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _score_all(
    cases: dict[str, ProdAskCase],
    observations: list[AskObservation],
    *,
    sql_oracle: dict,
    manifest_resources: frozenset[str],
) -> tuple[list[ProdAskCaseScore], list[dict]]:
    by_case: dict[str, dict[str, AskObservation]] = {}
    for observation in observations:
        by_case.setdefault(observation.case_id, {})[observation.transport] = observation

    scores: list[ProdAskCaseScore] = []
    parity: list[dict] = []
    for case_id, per_transport in sorted(by_case.items()):
        case = cases[case_id]
        oracle_id = case.accuracy.oracle.sql_case_id if case.accuracy else None
        for observation in per_transport.values():
            score = score_observation(
                case,
                observation,
                sql_oracle=sql_oracle.get(oracle_id) if oracle_id else None,
                manifest_resources=manifest_resources,
            )
            twin = (by_case.get(case.pair_id) or {}).get(observation.transport)
            if twin is not None:
                add_permission_variance(score, case, observation, twin)
            scores.append(score)
        if "json" in per_transport and "sse" in per_transport:
            result = transport_parity(per_transport["json"], per_transport["sse"])
            parity.append({"case_id": case_id, "passed": result.passed, "detail": result.detail})
    return scores, parity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("stub", "replay", "live"), default="stub")
    parser.add_argument("--transport", choices=("json", "sse", "both"), default="both")
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--oracle", default=str(ORACLE_PATH))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--only", help="comma-separated case ids")
    parser.add_argument("--answers", help="recorded observations jsonl, required for replay")
    parser.add_argument("--record", action="store_true", help="write observations for later replay")
    parser.add_argument("--judge", action="store_true", help="score the judged dimensions (paid)")
    parser.add_argument("--confirm-spend", action="store_true")
    parser.add_argument("--run-id")
    args = parser.parse_args()

    if args.mode == "live" and not args.confirm_spend:
        raise SystemExit("live mode makes paid model calls; pass --confirm-spend")
    if args.judge and not args.confirm_spend:
        raise SystemExit("--judge makes paid model calls; pass --confirm-spend")
    if args.judge:
        # The judge client, canary set, and rubric all exist. Wiring them to a
        # provider is a spend decision that has not been taken, so the run
        # refuses rather than quietly scoring nothing.
        raise SystemExit(
            "judged dimensions are not wired to a provider in this revision; "
            "see docs/superpowers/plans/2026-08-18-prod-ask-quality-suite.md"
        )
    if args.mode == "replay" and not args.answers:
        raise SystemExit("replay mode needs --answers")

    cases = load_cases(args.cases)
    preflight = _preflight(args, cases)
    selected = _selected(cases, args)
    preflight["selected_case_ids"] = sorted(selected)
    preflight["skipped_case_ids"] = sorted(set(cases) - set(selected))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or f"pa-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = out_dir / run_id
    if run_dir.exists():
        raise SystemExit(f"PREFLIGHT FAIL: output already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    preflight["run_id"] = run_id
    (run_dir / "preflight.json").write_text(
        json.dumps(preflight, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )

    transports = ["json", "sse"] if args.transport == "both" else [args.transport]
    if args.mode == "replay":
        recorded = _load_recorded(Path(args.answers))
        observations = [o for o in recorded if o.case_id in selected and o.transport in transports]
    else:
        client = TestClient(__import__("app.main", fromlist=["app"]).app)
        observations = [
            observation
            for case in selected.values()
            for observation in _observe(client, case, args.mode, transports)
        ]

    if args.record:
        (run_dir / "observations.jsonl").write_text(
            "".join(o.model_dump_json() + "\n" for o in observations), encoding="utf-8"
        )

    manifest_resources = frozenset(preflight.get("manifest_resources") or ())
    scores, parity = _score_all(
        cases,
        observations,
        sql_oracle=load_sql_oracle(args.oracle),
        manifest_resources=manifest_resources,
    )

    (run_dir / "scores.jsonl").write_text(
        "".join(score.model_dump_json() + "\n" for score in scores), encoding="utf-8"
    )
    summary = summarize(scores)
    summary["run_id"] = run_id
    summary["preflight"] = preflight
    summary["transport_parity"] = parity
    summary["parity_failures"] = [row["case_id"] for row in parity if not row["passed"]]
    (run_dir / "summary.json").write_text(
        json.dumps(seal_summary(summary), indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )

    written = write_failures(cases, scores, out_dir=FAILURE_DIR, run_id=run_id)

    print(f"run_id: {run_id}")
    print(f"observations: {summary['observations']} over {summary['cases_scored']} case(s)")
    print(f"deterministic passed: {summary['deterministic_passed']}/{summary['observations']}")
    print(f"resolved passed: {summary['resolved_passed']}/{summary['resolved']}")
    print(f"failing cases: {summary['failing_cases']}")
    print(f"parity failures: {summary['parity_failures']}")
    print(f"degraded checks: {summary['degraded_checks']}")
    print(f"skipped checks: {json.dumps(summary['skipped_checks'], indent=2)}")
    print(f"G4 failure files: {len(written)}")
    print(f"artifacts: {run_dir}")
    return 1 if summary["failing_cases"] or summary["parity_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
