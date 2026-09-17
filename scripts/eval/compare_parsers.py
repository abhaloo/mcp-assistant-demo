"""Compare parsers end-to-end and apply the pre-committed swap gate.

For each parser: run Test A (parse fidelity) and Test B (RAGAS over the same
docs after ingesting with that parser), then print a comparison table and the
gate decision (baseline = first parser).

Usage:
    python scripts/eval/compare_parsers.py --baseline pypdf --candidate unstructured

WARNING: --candidate unstructured calls the Unstructured API AND the RAGAS
judge LLM. Run under the rag-experiment spend gate.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from app.eval.parsing.metrics import decide_gate

ROOT = Path(__file__).resolve().parents[2]


def _parse_fidelity(parser_kind: str) -> dict:
    out = ROOT / f"evals/runs/parse_{parser_kind}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "scripts/eval/evaluate_parsing.py",
            "--parser",
            parser_kind,
            "--out",
            str(out),
        ],
        check=True,
        cwd=ROOT,
    )
    return json.loads(out.read_text(encoding="utf-8"))


def _ragas(parser_kind: str) -> dict:
    collection = f"parse_eval_{parser_kind}"
    out = ROOT / f"evals/runs/parse_eval_{parser_kind}.json"
    subprocess.run(
        [
            sys.executable,
            "scripts/deploy/ingest.py",
            "--parser",
            parser_kind,
            "--collection",
            collection,
        ],
        check=True,
        cwd=ROOT,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/eval/evaluate_ragas.py",
            "--type",
            "semantic",
            "--collection",
            collection,
            "--out",
            str(out),
        ],
        check=True,
        cwd=ROOT,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    return {m: v["mean_optimistic"] for m, v in data["aggregate"].items()}


def main():
    ap = argparse.ArgumentParser(description="Parser comparison + gate")
    ap.add_argument("--baseline", default="pypdf")
    ap.add_argument("--candidate", default="unstructured")
    ap.add_argument("--max-ragas-regression", type=float, default=0.02)
    ap.add_argument("--skip-ragas", action="store_true", help="parse fidelity only")
    args = ap.parse_args()

    rows = {}
    for kind in (args.baseline, args.candidate):
        print(f"\n=== {kind} ===")
        parse = _parse_fidelity(kind)
        row = {"mean_teds": parse["mean_teds"], "reading_order": parse["reading_order"]}
        if not args.skip_ragas:
            row.update(_ragas(kind))
        rows[kind] = row

    print("\n| parser | mean_teds | reading_order | faithfulness | context_precision |")
    print("|---|---|---|---|---|")
    for kind, r in rows.items():
        print(
            f"| {kind} | {r.get('mean_teds', 0):.3f} | {r.get('reading_order', 0):.3f} "
            f"| {r.get('faithfulness', '—')} | {r.get('context_precision', '—')} |"
        )

    if not args.skip_ragas and rows[args.candidate].get("faithfulness") is not None:
        decision = decide_gate(rows[args.baseline], rows[args.candidate], args.max_ragas_regression)
        print(f"\nGATE: {'ADOPT' if decision['adopt'] else 'REJECT'} — {decision['reason']}")
    else:
        print("\nGATE: skipped (RAGAS scores not recorded)")


if __name__ == "__main__":
    main()
