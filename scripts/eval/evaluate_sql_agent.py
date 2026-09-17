"""SQL agent diagnostic + regression harness.

Usage:
    python scripts/eval/evaluate_sql_agent.py --verify-gold          # check gold SQL only
    python scripts/eval/evaluate_sql_agent.py --repeats 3 --out evals/runs/sql/run.json
    python scripts/eval/evaluate_sql_agent.py --role finance --split dev

Points the SQL agent at EVAL_SNAPSHOT_DATABASE_URL (frozen restore), never the tunnel.
Persists only PII-safe fields (counts/match/timing/scrubbed SQL) — never result rows.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.config import settings
from app.eval.sql.snapshot import (
    assess_gold_degeneracy,
    point_agent_at_snapshot,
)
from app.paths import REPO_ROOT

# Re-exported from app.eval.sql.snapshot for backward-compatible imports.
__all__ = ["assess_gold_degeneracy", "point_agent_at_snapshot"]
_point_agent_at_snapshot = point_agent_at_snapshot

CASES = REPO_ROOT / "evals" / "sql" / "cases.jsonl"

# Public list pricing for gpt-4o-mini (USD per 1M tokens) — used only for a rough
# cost estimate in the summary; actual cost depends on the provider/endpoint.
# Cached input bills at 50% of standard input ($0.075 vs $0.15); cached_tokens is a
# SUBSET of prompt_tokens, so the discounted formula is
#   (prompt - cached)*PROMPT + cached*CACHED_PROMPT + completion*COMPLETION.
# When nothing is cached (cached=0) this equals the old prompt*PROMPT formula, so
# prior runs stay comparable. (OpenAI prompt-caching + pricing docs, 2026-06.)
_USD_PER_1M_PROMPT = 0.15
_USD_PER_1M_CACHED_PROMPT = 0.075
_USD_PER_1M_COMPLETION = 0.60


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def load_cases(role: str | None, split: str, cases_path: Path = CASES) -> list[dict]:
    rows = [
        json.loads(line)
        for line in cases_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if role:
        rows = [r for r in rows if r.get("role") == role]
    if split != "all":
        rows = [r for r in rows if r.get("split", "dev") == split]
    return rows


def verify_gold(cases: list[dict]) -> int:
    """Execute every gold SQL; report errors + empty results. Returns problem count."""
    from app.eval.sql.agent.access import ScopedSqlPolicy, ensure_scoped_sql_access
    from app.eval.sql.agent.agent import build_sql_database
    from app.eval.sql.diagnostics import _fetch
    from app.rag.access_tiers import get_access_tiers

    # Explicit CLI/eval/test exemption — see app/eval/sql/agent/access.py.
    ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())

    problems = 0
    for c in cases:
        tiers = get_access_tiers(c["role"], c.get("permissions", []))
        db, _ = build_sql_database(tiers, c["role"])
        try:
            rows = _fetch(db, c["gold_sql"])
        except Exception as exc:  # noqa: BLE001
            print(f"  GOLD ERROR {c['id']}: {type(exc).__name__}: {exc}")
            problems += 1
            continue
        candidate, reason = assess_gold_degeneracy(rows)
        if candidate:
            print(f"  GOLD DEGENERATE CANDIDATE {c['id']}: {reason} — confirm before excluding")
            problems += 1
        else:
            print(f"  ok {c['id']}: {len(rows)} row(s)")
    return problems


def _gold_degeneracy_metadata(cases: list[dict]) -> dict[str, dict]:
    """Per-case degeneracy flags from gold SQL shape (for aggregate output)."""
    from app.eval.sql.agent.access import ScopedSqlPolicy, ensure_scoped_sql_access
    from app.eval.sql.agent.agent import build_sql_database
    from app.eval.sql.diagnostics import _fetch
    from app.rag.access_tiers import get_access_tiers

    # Explicit CLI/eval/test exemption — see app/eval/sql/agent/access.py.
    ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())

    out: dict[str, dict] = {}
    for c in cases:
        tiers = get_access_tiers(c["role"], c.get("permissions", []))
        db, _ = build_sql_database(tiers, c["role"])
        try:
            rows = _fetch(db, c["gold_sql"])
        except Exception:  # noqa: BLE001
            out[c["id"]] = {"degenerate_candidate": False, "degenerate_reason": None}
            continue
        candidate, reason = assess_gold_degeneracy(rows)
        out[c["id"]] = {
            "degenerate_candidate": candidate,
            "degenerate_reason": reason,
        }
    return out


def aggregate(per_run: list[dict], cases: list[dict], repeats: int) -> dict:
    # Infra failures (rate-limit/timeout) are NOT model misses — exclude them from every
    # accuracy/latency/cost statistic, but keep them in the raw "runs" dump + a count.
    eval_runs = [r for r in per_run if not r.get("infra_error")]

    by_case: dict[str, list[dict]] = {c["id"]: [] for c in cases}
    for r in eval_runs:
        by_case[r["case_id"]].append(r)

    gold_meta = _gold_degeneracy_metadata(cases)
    degenerate_candidates = [
        cid for cid, meta in gold_meta.items() if meta.get("degenerate_candidate")
    ]

    cases_out, flaky = [], []
    for cid, runs in by_case.items():
        n = len(runs)
        passes = sum(1 for r in runs if r["match"])
        pass_rate = passes / n if n else 0.0
        is_flaky = 0 < passes < n
        if is_flaky:
            flaky.append(cid)
        meta = gold_meta.get(cid, {})
        cases_out.append(
            {
                "case_id": cid,
                "pass_rate": round(pass_rate, 3),
                "evaluated_runs": n,
                "flaky": is_flaky,
                "degenerate_candidate": meta.get("degenerate_candidate", False),
                "degenerate_reason": meta.get("degenerate_reason"),
                "valid_sql_rate": round(sum(r["valid_sql"] for r in runs) / n, 3) if n else 0.0,
                "median_latency_ms": (
                    round(statistics.median(r["latency_total_ms"] for r in runs), 1) if n else 0.0
                ),
                "median_sql_attempts": (
                    statistics.median(r["n_sql_attempts"] for r in runs) if n else 0
                ),
            }
        )

    scored_cases = [c for c in cases_out if c["evaluated_runs"] > 0]
    latencies = sorted(r["latency_total_ms"] for r in eval_runs)

    def pct(q: float) -> float:
        return latencies[min(len(latencies) - 1, int(q * len(latencies)))] if latencies else 0

    tp = sum(r.get("tokens_prompt", 0) for r in eval_runs)
    tc = sum(r.get("tokens_completion", 0) for r in eval_runs)
    tcache = sum(r.get("tokens_cached", 0) for r in eval_runs)
    # cache_field_seen=False across ALL runs means the endpoint never surfaced a cache
    # field (e.g. an OpenAI-compatible passthrough stripping prompt_tokens_details) —
    # which is NOT the same as "no cache hits". Distinguishes can't-measure from no-hit.
    cache_field_seen = any(r.get("cache_field_seen") for r in eval_runs)

    esc = settings.azure_chat_escalation_deployment
    escalated = [r for r in eval_runs if r.get("chat_deployment") == esc]
    escalation_rate = round(len(escalated) / len(eval_runs), 3) if eval_runs else 0.0
    escalated_cases = sorted({r["case_id"] for r in escalated})

    return {
        "model": settings.active_chat_model,
        "git_sha": _git_sha(),
        "n_cases": len(cases),
        "n_repeats": repeats,
        "infra_errors": len(per_run) - len(eval_runs),
        "snapshot_exec_accuracy": (
            round(statistics.mean(c["pass_rate"] for c in scored_cases), 3) if scored_cases else 0.0
        ),
        "metric_note": (
            "snapshot execution accuracy — diagnosis-grade. A single frozen DB can pass a "
            "logically-wrong query by coincidence, so a manual false-positive audit of "
            "passing cases is required before any model-comparison claim (see spec 3a). "
            "Infra failures (rate-limit/timeout) are excluded, not scored as misses."
        ),
        "valid_sql_rate": (
            round(statistics.mean(r["valid_sql"] for r in eval_runs), 3) if eval_runs else 0.0
        ),
        "p50_latency_ms": pct(0.50),
        "p95_latency_ms": pct(0.95),
        "mean_sql_attempts": (
            round(statistics.mean(r["n_sql_attempts"] for r in eval_runs), 2) if eval_runs else 0
        ),
        "mean_llm_latency_ms": (
            round(statistics.mean(r["latency_llm_ms"] for r in eval_runs), 1) if eval_runs else 0
        ),
        "safety": {
            "dml_attempts": sum(1 for r in eval_runs if r.get("dml_attempted")),
            "out_of_tier_refs": sum(1 for r in eval_runs if r.get("out_of_tier_table_ref")),
        },
        "cost": {
            "total_prompt_tokens": tp,
            "total_completion_tokens": tc,
            "total_cached_prompt_tokens": tcache,
            "cache_hit_rate": round(tcache / tp, 4) if tp else 0.0,
            "cache_field_seen": cache_field_seen,
            "mean_tokens_per_run": round((tp + tc) / len(eval_runs), 1) if eval_runs else 0,
            "est_usd_total": round(
                (tp - tcache) / 1e6 * _USD_PER_1M_PROMPT
                + tcache / 1e6 * _USD_PER_1M_CACHED_PROMPT
                + tc / 1e6 * _USD_PER_1M_COMPLETION,
                4,
            ),
            "est_usd_note": (
                "estimate at gpt-4o-mini public list pricing ($0.15/$0.075 cached/$0.60 "
                "per 1M in/cached-in/out); cached is a subset of prompt. cache_field_seen="
                "False means the endpoint never reported a cache field (can't-measure, "
                "not no-hit). Actual cost depends on the provider."
            ),
        },
        "flaky_cases": flaky,
        "degenerate_candidates": degenerate_candidates,
        "escalation_rate": escalation_rate,
        "escalated_cases": escalated_cases,
        "cases": cases_out,
        "runs": per_run,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="SQL agent diagnostic harness")
    ap.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="runs per case (>=3 for decisions, 5 for the gate; 1 disables bootstrap)",
    )
    ap.add_argument("--role", default=None, help="only cases with this role")
    ap.add_argument("--split", default="all", choices=["all", "dev", "holdout"])
    ap.add_argument("--verify-gold", action="store_true", help="check gold SQL only, then exit")
    ap.add_argument("--cases", default=str(CASES), help="path to cases JSONL (dev or holdout)")
    ap.add_argument("--out", default=None, help="write summary JSON here")
    ap.add_argument("--concurrency", type=int, default=8, help="parallel agent runs (thread pool)")
    ap.add_argument(
        "--capture-failures",
        action="store_true",
        help="write evals/failures/<run-id>_<case-id>_<mode>.json for low scorers (G4)",
    )
    ap.add_argument(
        "--run-id", default=None, help="failure-record run id (default: git_sha+timestamp)"
    )
    args = ap.parse_args()

    # RuntimeError -> sys.exit keeps this CLI's original behaviour after the helper
    # moved to app/eval/sql/snapshot.py (the moved version raises instead of exiting,
    # because a library must not kill its caller's process).
    try:
        point_agent_at_snapshot(args.concurrency)
    except RuntimeError as exc:
        sys.exit(str(exc))
    cases = load_cases(args.role, args.split, Path(args.cases))
    if not cases:
        sys.exit("no cases matched the filters")

    if args.verify_gold:
        print(f"=== verify-gold ({len(cases)} cases) ===")
        problems = verify_gold(cases)
        print(f"\n{problems} problem(s)")
        sys.exit(1 if problems else 0)

    from app.eval.sql.diagnostics import run_case

    work = [c for c in cases for _ in range(args.repeats)]
    print(
        f"=== SQL agent eval: {len(cases)} cases x {args.repeats} repeats "
        f"({len(work)} runs, concurrency={args.concurrency}) ==="
    )
    start = time.time()
    per_run = []
    fold_ids = frozenset(c["id"] for c in cases)
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [ex.submit(run_case, c, fold_ids) for c in work]
        for done, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            per_run.append(r)
            tag = f" INFRA={r['infra_error']}" if r.get("infra_error") else ""
            print(
                f"  [{done}/{len(work)}] {r['case_id']} match={r['match']} "
                f"valid={r['valid_sql']} attempts={r['n_sql_attempts']} "
                f"{r['latency_total_ms']:.0f}ms {r['reason']}{tag}"
            )
    wall = time.time() - start

    summary = aggregate(per_run, cases, args.repeats)
    summary["concurrency"] = args.concurrency
    summary["wall_clock_s"] = round(wall, 1)

    if args.capture_failures:
        from app.eval.failure_store import write_failures

        run_id = args.run_id or f"{summary['git_sha']}-{time.strftime('%Y%m%dT%H%M%S')}"
        paths = write_failures(
            summary,
            cases,
            out_dir=settings.failure_store_dir,
            run_id=run_id,
            mode=args.split,
            source="eval",
        )
        print(f"captured {len(paths)} eval failure record(s) -> {settings.failure_store_dir}")

    print(
        f"\nsnapshot_exec_accuracy={summary['snapshot_exec_accuracy']:.3f}  "
        f"valid_sql={summary['valid_sql_rate']:.3f}  "
        f"p95={summary['p95_latency_ms']:.0f}ms  "
        f"mean_attempts={summary['mean_sql_attempts']}"
    )
    s = summary["safety"]
    if s["dml_attempts"] or s["out_of_tier_refs"]:
        print(f"SAFETY: dml_attempts={s['dml_attempts']} out_of_tier_refs={s['out_of_tier_refs']}")
    if summary["infra_errors"]:
        print(f"INFRA ERRORS (excluded from accuracy): {summary['infra_errors']}")
    print(
        f"escalation_rate={summary['escalation_rate']:.3f}  "
        f"escalated_cases={summary['escalated_cases']}"
    )
    c = summary["cost"]
    print(
        f"tokens: {c['total_prompt_tokens']}+{c['total_completion_tokens']} "
        f"(mean {c['mean_tokens_per_run']}/run)  est ${c['est_usd_total']}"
    )
    if c["cache_field_seen"]:
        print(
            f"prompt-cache: {c['total_cached_prompt_tokens']} cached tokens "
            f"({c['cache_hit_rate']:.1%} of prompt) — endpoint reports cache data"
        )
    else:
        print(
            "prompt-cache: endpoint reported NO cache field on any call "
            "(can't-measure here, not necessarily no-hit)"
        )
    if summary["flaky_cases"]:
        print(f"FLAKY (non-deterministic): {summary['flaky_cases']}")
    print(f"({len(work)} runs in {wall:.1f}s at concurrency={args.concurrency})")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
