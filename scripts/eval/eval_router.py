"""Deterministic router-robustness scorer (local-only; zero LLM/DB calls, $0).

Scores app.rag.model_router.is_hard_financial against the hand-labeled
evals/sql/router_cases.jsonl: escalation recall (PRIMARY) + precision + per-stratum
misses. With --audit, cross-checks the labels against observed mini-vs-escalation
outcomes in existing run files (reuses runs; never spends). See
docs/experiments/router-robustness/design.md.

Usage:
    ./.venv/Scripts/python.exe scripts/eval/eval_router.py
    ./.venv/Scripts/python.exe scripts/eval/eval_router.py --audit
    ./.venv/Scripts/python.exe scripts/eval/eval_router.py --json
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Callable
from pathlib import Path

from app.rag.model_router import is_hard_financial

_DEFAULT_RUN_DIR = Path("evals/runs/sql")
_MINI_RUNS = ["casc-gated-dev-5x.json", "casc-gated-holdout-5x.json"]
_ESC_RUNS = ["b2-grounding-dev-5x.json", "b2-grounding-holdout-5x.json"]
_GOLD_FILES = ["evals/sql/cases.jsonl", "evals/sql/cases_holdout.jsonl"]


def load_router_cases(path: str | Path) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson 95% score interval for a binomial proportion k/n.

    Honest expression of how little a fixed n-case set determines production recall: the
    router is deterministic (no run-to-run noise on THIS set), so the interval reflects the
    sampling uncertainty of the set itself, not the regex.
    """
    if n == 0:
        return None
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return (round(center - half, 4), round(center + half, 4))


def score_router(cases: list[dict], predict: Callable[[str], bool] = is_hard_financial) -> dict:
    tp = fp = fn = tn = 0
    misses: list[tuple[str, str]] = []
    false_escalations: list[tuple[str, str]] = []
    strata: dict[str, dict] = {}
    for c in cases:
        should = bool(c["should_escalate"])
        pred = bool(predict(c["question"]))
        st = strata.setdefault(c["stratum"], {"n": 0, "n_pos": 0, "tp": 0, "misses": []})
        st["n"] += 1
        if should:
            st["n_pos"] += 1
        if should and pred:
            tp += 1
            st["tp"] += 1
        elif should and not pred:
            fn += 1
            misses.append((c["id"], c["question"]))
            st["misses"].append(c["id"])
        elif not should and pred:
            fp += 1
            false_escalations.append((c["id"], c["question"]))
        else:
            tn += 1

    n_pos = tp + fn
    n_esc = tp + fp
    by_stratum = {
        k: {
            "n": v["n"],
            "n_pos": v["n_pos"],
            "recall": round(v["tp"] / v["n_pos"], 4) if v["n_pos"] else None,
            "misses": v["misses"],
        }
        for k, v in strata.items()
    }
    return {
        "n": len(cases),
        "n_pos": n_pos,
        "n_neg": tn + fp,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "recall": round(tp / n_pos, 4) if n_pos else None,
        "recall_ci95": wilson_ci(tp, n_pos),
        "precision": round(tp / n_esc, 4) if n_esc else None,
        "misses": misses,
        "false_escalations": false_escalations,
        "by_stratum": by_stratum,
    }


def _load_gold_questions(paths=_GOLD_FILES) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in paths:
        path = Path(p)
        # Holdout gold (cases_holdout.jsonl) is gitignored/local-only, so it is absent in a
        # fresh clone / CI. Gold is used ONLY to annotate case_ids with question text, so a
        # missing file degrades to "<unknown>" rather than crashing the audit.
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["id"]] = r["question"]
    return out


def _pass_rates_for_deployment(data: dict, *, escalated: bool) -> dict[str, float]:
    """case_id -> pass_rate, restricted to cases that ran on the (escalated|default) deployment
    in this run. Within one run file a case uses a single deployment (deterministic router).
    Takes the already-parsed run dict so the caller reads each file once."""
    esc_set = set(data.get("escalated_cases") or [])
    rates: dict[str, float] = {}
    for c in data.get("cases", []):
        # evaluated_runs<=0 (or key absent) = case didn't run here -> skip (tolerant sentinel)
        if c.get("evaluated_runs", 0) <= 0:
            continue
        ran_escalated = c["case_id"] in esc_set
        if ran_escalated == escalated:
            rates[c["case_id"]] = c["pass_rate"]
    return rates


def _needs_escalation_from_rates(
    mini: dict[str, float],
    esc: dict[str, float],
    questions: dict[str, str],
    threshold: float = 0.5,
) -> tuple[list[dict], list[str]]:
    """Cases that fail on mini AND pass on escalation -> empirically need escalation.

    Returns (needs, gaps). 'gaps' = case_ids that fail on mini but lack an escalation
    outcome to compare (so we can't conclude) — reported, never spent on.
    """
    needs: list[dict] = []
    gaps: list[str] = []
    for cid, mrate in mini.items():
        if mrate >= threshold:
            continue  # passes on mini -> doesn't need escalation
        if cid in esc:
            if esc[cid] >= threshold:
                q = questions.get(cid)
                needs.append(
                    {
                        "case_id": cid,
                        "question": q or "<unknown>",
                        "mini_pass": mrate,
                        "esc_pass": esc[cid],
                        "router_escalates": is_hard_financial(q or ""),
                    }
                )
        else:
            gaps.append(cid)  # fails mini, no escalation outcome to compare
    return needs, gaps


def run_empirical_audit(run_dir: Path = _DEFAULT_RUN_DIR) -> dict:
    questions = _load_gold_questions()
    mini: dict[str, float] = {}
    escalated_in_mini_source: set[str] = set()
    for f in _MINI_RUNS:
        data = json.loads((run_dir / f).read_text(encoding="utf-8"))
        # cases that escalated in the mini-SOURCE run never ran on mini -> no mini outcome
        escalated_in_mini_source |= set(data.get("escalated_cases") or [])
        mini.update(_pass_rates_for_deployment(data, escalated=False))
    esc: dict[str, float] = {}
    for f in _ESC_RUNS:
        data = json.loads((run_dir / f).read_text(encoding="utf-8"))
        esc.update(_pass_rates_for_deployment(data, escalated=True))
    # Fail loud if a run file was present but malformed/schema-drifted (loaded 0 outcomes): a
    # silent empty side would make the audit print "no contradictions" with no evidence at all.
    if not mini or not esc:
        raise SystemExit(
            f"audit: loaded {len(mini)} mini + {len(esc)} escalation outcomes — a run file is "
            "empty or its schema drifted (expected cases[] / escalated_cases). Aborting."
        )
    needs, gaps = _needs_escalation_from_rates(mini, esc, questions)
    contradictions = [n for n in needs if not n["router_escalates"]]
    no_mini_evidence = sorted(escalated_in_mini_source - set(mini))  # never observed on mini
    return {
        "needs_escalation": needs,
        "contradictions": contradictions,
        "audit_gaps": sorted(gaps),
        "no_mini_evidence": no_mini_evidence,
        "coverage_note": (
            f"{len(needs)} case(s) have fail-mini & pass-esc evidence; "
            f"{len(no_mini_evidence)} case(s) escalated everywhere -> no mini outcome, "
            f"empirically un-auditable. 'No contradictions' covers ONLY the evidenced cases."
        ),
    }


def _print_report(result: dict) -> None:
    print("=== Router-robustness baseline (is_hard_financial vs router_cases.jsonl) ===")
    print(f"  cases: {result['n']}  (positives {result['n_pos']}, negatives {result['n_neg']})")
    print(
        f"  escalation RECALL:    {result['recall']}  Wilson95 {result['recall_ci95']}   "
        f"<-- PRIMARY coverage indicator (TP={result['tp']} FN={result['fn']})"
    )
    print(f"  escalation precision: {result['precision']}   (FP={result['fp']} TN={result['tn']})")
    print("  per-stratum recall:")
    for st, v in sorted(result["by_stratum"].items()):
        print(f"    {st:28s} recall={v['recall']}  (n_pos={v['n_pos']}) misses={v['misses']}")
    if result["misses"]:
        print("  MISSES (should escalate, router did NOT):")
        for cid, q in result["misses"]:
            print(f"    {cid}: {q!r}")
    if result["false_escalations"]:
        print("  FALSE ESCALATIONS (should NOT escalate, router did):")
        for cid, q in result["false_escalations"]:
            print(f"    {cid}: {q!r}")


def _print_audit(audit: dict) -> None:
    n_needs = len(audit["needs_escalation"])
    print("\n=== Empirical label audit (existing runs; $0) ===")
    print(f"  cases empirically needing escalation (fail-mini & pass-esc): {n_needs}")
    for n in audit["needs_escalation"]:
        flag = "  <-- ROUTER MISSES THIS" if not n["router_escalates"] else ""
        print(
            f"    {n['case_id']}: mini={n['mini_pass']:.2f} esc={n['esc_pass']:.2f} "
            f"router_escalates={n['router_escalates']}{flag}"
        )
    if audit["contradictions"]:
        ids = [c["case_id"] for c in audit["contradictions"]]
        print(f"  CONTRADICTIONS (empirically need escalation but router does NOT): {ids}")
    else:
        print("  no contradictions AMONG EVIDENCED CASES (router escalates each)")
    if audit["no_mini_evidence"]:
        nme = audit["no_mini_evidence"]
        print(f"  no-mini-evidence (escalated everywhere; un-auditable): {nme}")
    if audit["audit_gaps"]:
        print(
            f"  audit-gaps (fail-mini, no escalation outcome to compare): {audit['audit_gaps']} "
            f"(do NOT spend to fill without sign-off)"
        )
    print(f"  COVERAGE: {audit['coverage_note']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="evals/sql/router_cases.jsonl")
    ap.add_argument("--audit", action="store_true", help="run the empirical label audit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cases = load_router_cases(args.cases)
    result = score_router(cases)
    audit = run_empirical_audit() if args.audit else None

    if args.json:
        print(json.dumps({"score": result, "audit": audit}, indent=2))
    else:
        _print_report(result)
        if audit is not None:
            _print_audit(audit)


if __name__ == "__main__":
    main()
