"""
SQL Anonymizer evaluation: measures leak rate and over-anonymization rate
of SqlAnonymizer.anonymize_rows against a labeled ground truth file.

Scores treatment-correctness (pseudonymize/redact/suppress), not merely
value-changed — per ADR 0025.

Usage:
    python scripts/eval/evaluate_sql_anonymization.py
    python scripts/eval/evaluate_sql_anonymization.py --verbose
"""

import argparse
import json
import re
import tempfile
from pathlib import Path

from app.config import settings
from app.eval.sql.agent.anonymizer import REDACT_MARKER, SUPPRESS_MARKER, SqlAnonymizer
from scripts.eval.evaluate_pii_detection import f_beta

GROUND_TRUTH_PATH = "tests/guardrails/fixtures/sql_anonymization_ground_truth.jsonl"
_TOKEN_RE = re.compile(r"^<[A-Z_]+_\d+>$")


def load_ground_truth() -> list[dict]:
    truth = []
    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            truth.append(json.loads(line))
    return truth


def _treatment_of(original, produced) -> str | None:
    if produced == original:
        return None
    if produced == SUPPRESS_MARKER:
        return "suppress"
    if produced == REDACT_MARKER:
        return "redact"
    if isinstance(produced, str) and _TOKEN_RE.match(produced):
        return "pseudonymize"
    return None


def evaluate_row(entry: dict) -> dict:
    anon = SqlAnonymizer(query_id=f"eval-{id(entry)}", role="sales")
    row, table = entry["row"], entry["table"]
    expected: dict = entry["expected_treatments"]
    out = anon.anonymize_rows([row], {table})
    if not out:
        return {"table": table, "tp": [], "fp": [], "fn": sorted(expected)}
    out_row = out[0]
    tp, fp, fn, seen = [], [], [], set()
    for col, want in expected.items():
        seen.add(col)
        (tp if _treatment_of(row.get(col), out_row.get(col)) == want else fn).append(col)
    for col, original in row.items():
        if col in seen or original is None or not isinstance(original, str):
            continue
        if _treatment_of(original, out_row.get(col)) is not None:
            fp.append(col)
    return {"table": table, "tp": sorted(tp), "fp": sorted(fp), "fn": sorted(fn)}


def main() -> None:
    parser = argparse.ArgumentParser(description="SQL Anonymizer evaluation")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-row TP/FP/FN columns",
    )
    args = parser.parse_args()

    settings.redaction_entities = ["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS"]
    with tempfile.TemporaryDirectory() as td:
        settings.redaction_audit_log_path = str(Path(td) / "eval_audit.jsonl")

        truth = load_ground_truth()
        if not truth:
            print(f"No entries in {GROUND_TRUTH_PATH}")
            return

        print(f"=== SQL Anonymizer Eval ({len(truth)} rows) ===\n")

        totals = {"tp": 0, "fp": 0, "fn": 0}
        for i, entry in enumerate(truth):
            res = evaluate_row(entry)
            tp_n, fp_n, fn_n = len(res["tp"]), len(res["fp"]), len(res["fn"])
            totals["tp"] += tp_n
            totals["fp"] += fp_n
            totals["fn"] += fn_n
            if args.verbose:
                print(f"  [{i}] {res['table']}: TP={res['tp']} FP={res['fp']} FN={res['fn']}")

        tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        leak_rate = fn / (tp + fn) if (tp + fn) > 0 else 0.0
        over_anon_rate = fp / (tp + fp) if (tp + fp) > 0 else 0.0
        f1 = f_beta(precision, recall, beta=1.0)
        f2 = f_beta(precision, recall, beta=2.0)

        print("\n=== Aggregate (column-level) ===")
        print(f"  TP={tp}  FP={fp}  FN={fn}")
        print(f"  Precision:           {precision:.3f}")
        print(f"  Recall:              {recall:.3f}")
        print(f"  Leak rate (FN/real): {leak_rate:.3f}  ← the dangerous one")
        print(f"  Over-anon rate:      {over_anon_rate:.3f}  ← annoying but recoverable")
        print(f"  F1:                  {f1:.3f}")
        print(f"  F2:                  {f2:.3f}  (recall-weighted, primary metric)")


if __name__ == "__main__":
    main()
