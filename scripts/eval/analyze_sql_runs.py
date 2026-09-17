"""Analyze + compare SQL-agent eval runs (tracked; stats live in app/eval/run_stats.py).

Reads the summary JSONs written by evaluate_sql_agent.py and reports exec-match
both on the full case set and on the *discriminating* set (human-confirmed degenerate
gold cases are carved out — see insights-log). For two runs (baseline vs candidate)
it computes a paired, case-level bootstrap 95% CI on the exec-match delta — the gate
statistic for §8.3b / §8.3c.

Usage:
    python scripts/eval/analyze_sql_runs.py summary FILE.json
    python scripts/eval/analyze_sql_runs.py compare BASE.json CAND.json
        [--label-base X --label-cand Y]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.eval.run_stats import (
    classify_moves,
    discordant_counts,
    mcnemar_exact_p,
    paired_bootstrap_ci,
    verdict,
)

# Human-confirmed degenerate gold on this snapshot — unanswerable, so any (even wrong)
# agent query can match by coincidence. Carved out of the headline; still reported
# separately. Gold NOT modified.
CONFIRMED_DEGENERATE = {"prod-jobs-delayed", "prod-dept-slowest", "h-fin-expense-month"}


def _case_rate(summary: dict) -> dict[str, float]:
    """case_id -> pass_rate over evaluated (non-infra) runs."""
    return {c["case_id"]: c["pass_rate"] for c in summary["cases"] if c["evaluated_runs"] > 0}


def _unconfirmed_degenerate_candidates(summary: dict) -> list[str]:
    """Cases flagged degenerate_candidate in the run but not yet human-confirmed."""
    confirmed = CONFIRMED_DEGENERATE
    top_level = set(summary.get("degenerate_candidates") or [])
    from_cases = {c["case_id"] for c in summary.get("cases", []) if c.get("degenerate_candidate")}
    return sorted((top_level | from_cases) - confirmed)


def _warn_unconfirmed_candidates(summary: dict) -> None:
    pending = _unconfirmed_degenerate_candidates(summary)
    if pending:
        print(
            "  WARNING: new degenerate candidate(s) — confirm before excluding: "
            + ", ".join(pending),
            file=sys.stderr,
        )


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def summarize(summary: dict, tag: str = "") -> None:
    _warn_unconfirmed_candidates(summary)
    rates = _case_rate(summary)
    disc = {k: v for k, v in rates.items() if k not in CONFIRMED_DEGENERATE}
    degen = {k: v for k, v in rates.items() if k in CONFIRMED_DEGENERATE}
    print(
        f"--- {tag or summary.get('git_sha', '?')} (model={summary.get('model')}, "
        f"sha={summary.get('git_sha')}, repeats={summary.get('n_repeats')}) ---"
    )
    print(
        f"  cases scored:        {len(rates)}  "
        f"(discriminating {len(disc)}, degenerate {len(degen)})"
    )
    print(f"  exec_match ALL:      {_mean(list(rates.values())):.3f}")
    print(f"  exec_match DISCRIM:  {_mean(list(disc.values())):.3f}   <-- headline")
    if degen:
        print(f"  degenerate cases:    {{{', '.join(f'{k}={v:.2f}' for k, v in degen.items())}}}")
    print(f"  valid_sql_rate:      {summary.get('valid_sql_rate')}")
    print(f"  mean_sql_attempts:   {summary.get('mean_sql_attempts')}")
    print(
        f"  p50/p95 latency ms:  {summary.get('p50_latency_ms')} / {summary.get('p95_latency_ms')}"
    )
    print(f"  infra_errors:        {summary.get('infra_errors')}")
    print(f"  flaky_cases:         {summary.get('flaky_cases')}")
    s = summary.get("safety", {})
    print(
        f"  safety:              dml={s.get('dml_attempts')} "
        f"out_of_tier={s.get('out_of_tier_refs')}"
    )
    c = summary.get("cost", {})
    print(
        f"  tokens (p+c):        {c.get('total_prompt_tokens')}+{c.get('total_completion_tokens')}"
        f"  est ${c.get('est_usd_total')}"
    )


def bootstrap_delta_ci(base: dict, cand: dict, n_boot: int = 10000, seed: int = 7) -> dict:
    """Paired case-level bootstrap of mean(cand - base) over shared discriminating cases."""
    br, cr = _case_rate(base), _case_rate(cand)
    shared = sorted((set(br) & set(cr)) - CONFIRMED_DEGENERATE)
    pairs = [(k, br[k], cr[k]) for k in shared]
    deltas = [cr[k] - br[k] for k in shared]
    ci = paired_bootstrap_ci(deltas, n_boot=n_boot, seed=seed)
    b, c = discordant_counts(pairs)
    moves = classify_moves(pairs)
    # Sub-threshold moves: per-case changes that DON'T cross the 0.5 binary boundary
    # (e.g. 1.0->0.6 lost-2-runs, 0.8->1.0 gained). Invisible to the McNemar split but
    # vital for the manual audit, so surface them too.
    binary_ids = {m[0] for m in moves["regressions"] + moves["improvements"]}
    sub_threshold = [
        (k, br[k], cr[k]) for k in shared if abs(cr[k] - br[k]) > 1e-9 and k not in binary_ids
    ]
    return {
        "n_shared_discriminating": ci["n"],
        "delta_point": round(ci["point"], 4),
        "ci95": (round(ci["lo"], 4), round(ci["hi"], 4)),
        "mcnemar_b": b,
        "mcnemar_c": c,
        "mcnemar_p": round(mcnemar_exact_p(b, c), 4),
        "regressions": moves["regressions"],
        "improvements": moves["improvements"],
        "sub_threshold_moves": sub_threshold,
        "verdict": verdict(ci["lo"], mcnemar_exact_p(b, c)),
    }


def compare(base: dict, cand: dict, label_base: str, label_cand: str) -> None:
    summarize(base, label_base)
    print()
    summarize(cand, label_cand)
    print()
    res = bootstrap_delta_ci(base, cand)
    print(f"=== DELTA  {label_cand} - {label_base}  (discriminating, paired) ===")
    print(f"  shared discriminating cases: {res['n_shared_discriminating']}")
    print(f"  exec_match delta (point):    {res['delta_point']:+.4f}")
    print(f"  bootstrap 95% CI:            [{res['ci95'][0]:+.4f}, {res['ci95'][1]:+.4f}]")
    print(
        f"  McNemar discordant (b/c):    {res['mcnemar_b']}/{res['mcnemar_c']}  (b=lost, c=gained)"
    )
    print(f"  McNemar exact p:             {res['mcnemar_p']:.4f}")
    print(f"  VERDICT:                     {res['verdict']}")
    print("  (SHIP gate = CI lower bound >= 0 [no regression] + production confirmation;")
    print("   SIGNIFICANT needs >=6 one-directional flips, rarely reachable at this n.)")
    if res["regressions"]:
        print("  regressions (case_id: base -> cand):")
        for k, b, c in sorted(res["regressions"], key=lambda t: t[2] - t[1]):
            print(f"     {k}: {b:.2f} -> {c:.2f}  ({c - b:+.2f})")
    else:
        print("  regressions: none")
    if res["improvements"]:
        print("  improvements (case_id: base -> cand):")
        for k, b, c in sorted(res["improvements"], key=lambda t: t[2] - t[1], reverse=True):
            print(f"     {k}: {b:.2f} -> {c:.2f}  ({c - b:+.2f})")
    if res["sub_threshold_moves"]:
        print("  sub-threshold moves (no 0.5 crossing — audit these too):")
        for k, b, c in sorted(res["sub_threshold_moves"], key=lambda t: t[2] - t[1]):
            print(f"     {k}: {b:.2f} -> {c:.2f}  ({c - b:+.2f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("summary")
    s1.add_argument("file")
    s1.add_argument("--tag", default="")
    s2 = sub.add_parser("compare")
    s2.add_argument("base")
    s2.add_argument("cand")
    s2.add_argument("--label-base", default="baseline")
    s2.add_argument("--label-cand", default="candidate")
    args = ap.parse_args()

    if args.cmd == "summary":
        summarize(json.loads(Path(args.file).read_text(encoding="utf-8")), args.tag)
    else:
        compare(
            json.loads(Path(args.base).read_text(encoding="utf-8")),
            json.loads(Path(args.cand).read_text(encoding="utf-8")),
            args.label_base,
            args.label_cand,
        )


if __name__ == "__main__":
    main()
