"""
PII detection evaluation: measures Presidio's precision/recall/F2 against
a labeled ground truth file (tests/guardrails/fixtures/pii_ground_truth.jsonl).

This is a SEPARATE eval from RAGAS. RAGAS measures answer quality with redaction
toggled. This script measures whether the redaction layer correctly identifies
PII spans in the corpus — independent of any LLM call.

Usage:
    python scripts/eval/evaluate_pii_detection.py

Why F2 not F1?
    Per Microsoft Presidio guidance: in PII detection, missing real PII (false
    negative) is much worse than over-redacting (false positive). F2 weights
    recall 2x precision, reflecting that asymmetry.
"""

import argparse
import json
import sys
from pathlib import Path

# Force UTF-8 on stdout/stderr so output containing em-dashes, ≤, → etc.
# doesn't crash on Windows' cp1252 console. Mirrors scripts/eval/evaluate_ragas.py.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


from app.config import settings  # noqa: E402
from app.guardrails.analyzer import get_analyzer  # noqa: E402
from app.guardrails.context_filter import filter_id_context  # noqa: E402

GROUND_TRUTH_PATH = "tests/guardrails/fixtures/pii_ground_truth.jsonl"
HOLDOUT_PATH = "tests/guardrails/fixtures/pii_filter_holdout.jsonl"


def load_ground_truth() -> list[dict]:
    """Load ground truth, skipping unreviewed entries."""
    truth = []
    with open(GROUND_TRUTH_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if not entry.get("reviewed"):
                continue
            truth.append(entry)
    return truth


def _normalize(text: str) -> str:
    """Normalize a span for matching: lowercase, collapse whitespace."""
    return " ".join(text.lower().split())


def f_beta(precision: float, recall: float, beta: float = 2.0) -> float:
    """
    Fβ score: weighted harmonic mean of precision and recall.
    β > 1 weights recall more (we use β=2 for PII per Microsoft guidance).

    """
    if precision == 0 and recall == 0:
        return 0.0
    fb = ((1 + beta**2) * precision * recall) / ((beta**2 * precision) + recall)
    return fb


def evaluate_file(
    source: str,
    expected_spans: list[dict],
    threshold: float = 0.0,
    apply_filter: bool = False,
) -> dict:
    """
    Run analyzer on one source file, count TP/FP/FN against expected spans.

    Returns: {"tp": int, "fp": int, "fn": int, "findings": int, "expected": int}
    """
    analyzer = get_analyzer()
    file_text = Path(source).read_text(encoding="utf-8")

    findings = analyzer.analyze(
        text=file_text,
        language="en",
        entities=settings.redaction_entities,
        score_threshold=threshold,
    )
    if apply_filter:
        findings = filter_id_context(file_text, findings)

    # Build matched expected-span set so we can count FN at the end.
    matched_expected: set[int] = set()
    tp = 0
    fp = 0

    for finding in findings:
        finding_text = file_text[finding.start : finding.end]
        # Walk expected_spans; if any matches, count TP and mark that span matched.
        # If none match, count FP.
        match_idx = None
        for i, span in enumerate(expected_spans):
            if span["type"] == finding.entity_type and _normalize(span["text"]) == _normalize(
                finding_text
            ):
                match_idx = i
                break

        if match_idx is not None:
            tp += 1
            matched_expected.add(match_idx)
        else:
            fp += 1

    fn = len(expected_spans) - len(matched_expected)

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "findings": len(findings),
        "expected": len(expected_spans),
    }


SWEEP_THRESHOLDS = [0.0, 0.4, 0.6, 0.8, 0.95]


# ---- Held-out filter eval (ISS-005) -------------------------------------
# Sentence-level eval that exercises the context_filter on text the filter
# author didn't see. See tests/guardrails/fixtures/pii_filter_holdout.jsonl for the dataset and
# docs/insights-log.md for the methodology rationale (closed-loop problem).


def _load_holdout() -> list[dict]:
    """Load the held-out sentences. Each entry has sentence/has_pii/pii_spans/category/trigger."""
    entries = []
    with open(HOLDOUT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def _classify_holdout_outcome(entry: dict, pre: list, post: list) -> str:
    """
    Classify one held-out sentence's outcome based on pre/post-filter findings.

    trap            -> tp | filter_fn | presidio_fn
    correct_suppress-> tn | filter_fp | presidio_miss
    control         -> tp | filter_overfire | presidio_fn
    """
    sentence = entry["sentence"]
    expected = entry["pii_spans"]

    def matches_expected(finding) -> bool:
        text = sentence[finding.start : finding.end]
        for span in expected:
            if span["type"] == finding.entity_type and _normalize(span["text"]) == _normalize(text):
                return True
        return False

    category = entry["category"]
    if category == "trap":
        if any(matches_expected(f) for f in post):
            return "tp"
        if any(matches_expected(f) for f in pre):
            return "filter_fn"  # filter wrongly suppressed real PII
        return "presidio_fn"  # Presidio never found it — not the filter's fault

    if category == "correct_suppress":
        if not pre:
            return "presidio_miss"  # Presidio didn't fire on the ID-shaped string
        if post:
            return "filter_fp"  # filter failed to suppress an ID
        return "tn"  # filter correctly suppressed

    if category == "control":
        # Filter must NOT drop anything. If post < pre, regex matched a non-trigger word.
        if len(post) < len(pre):
            return "filter_overfire"
        if any(matches_expected(f) for f in post):
            return "tp"
        return "presidio_fn"

    raise ValueError(f"unknown category: {category}")


def run_holdout_eval(threshold: float = 0.0) -> dict:
    """Run the held-out filter eval. Returns per-category counters + headline rates.

    Programmatic API used by both the CLI and tests/guardrails/test_filter_holdout.py.
    """
    analyzer = get_analyzer()
    entries = _load_holdout()

    by_category: dict[str, list[str]] = {"trap": [], "correct_suppress": [], "control": []}
    for entry in entries:
        sentence = entry["sentence"]
        pre = analyzer.analyze(
            text=sentence,
            language="en",
            entities=settings.redaction_entities,
            score_threshold=threshold,
        )
        post = filter_id_context(sentence, pre)
        outcome = _classify_holdout_outcome(entry, pre, post)
        by_category[entry["category"]].append(outcome)

    # Aggregate per category.
    def _counts(outcomes: list[str], keys: tuple) -> dict:
        return {k: sum(1 for o in outcomes if o == k) for k in keys}

    trap = _counts(by_category["trap"], ("tp", "filter_fn", "presidio_fn"))
    cs = _counts(by_category["correct_suppress"], ("tn", "filter_fp", "presidio_miss"))
    ctrl = _counts(by_category["control"], ("tp", "filter_overfire", "presidio_fn"))

    trap_evaluable = trap["tp"] + trap["filter_fn"]
    cs_evaluable = cs["tn"] + cs["filter_fp"]
    ctrl_n = sum(ctrl.values())

    return {
        "trap": {**trap, "evaluable": trap_evaluable, "n": len(by_category["trap"])},
        "correct_suppress": {
            **cs,
            "evaluable": cs_evaluable,
            "n": len(by_category["correct_suppress"]),
        },
        "control": {**ctrl, "n": ctrl_n},
        "rates": {
            "over_suppression": trap["filter_fn"] / trap_evaluable if trap_evaluable else 0.0,
            "suppression_precision": cs["tn"] / cs_evaluable if cs_evaluable else 0.0,
            "false_fire": ctrl["filter_overfire"] / ctrl_n if ctrl_n else 0.0,
        },
    }


def _print_holdout_report(result: dict) -> None:
    trap, cs, ctrl, rates = (
        result["trap"],
        result["correct_suppress"],
        result["control"],
        result["rates"],
    )
    print("\n=== Held-out Filter Eval (ISS-005) ===\n")

    print("Category 1 — Trap (real phone near trigger; filter must NOT suppress)")
    print(
        f"  N = {trap['n']} | filter-evaluable = {trap['evaluable']} "
        f"(presidio missed {trap['presidio_fn']})"
    )
    print(f"  TP  (phone survived):        {trap['tp']}")
    print(f"  FN  (filter wrongly dropped):{trap['filter_fn']}")
    print(f"  Over-suppression rate:       {rates['over_suppression']:.1%}   target ≤ 5%")

    print("\nCategory 2 — Correct suppress (ID-shaped number; filter SHOULD suppress)")
    print(
        f"  N = {cs['n']} | filter-evaluable = {cs['evaluable']} "
        f"(presidio missed {cs['presidio_miss']})"
    )
    print(f"  TN  (correctly suppressed):  {cs['tn']}")
    print(f"  FP  (filter failed):         {cs['filter_fp']}")
    print(f"  Suppression precision:       {rates['suppression_precision']:.1%}   target ≥ 80%")

    print("\nCategory 3 — Control (no trigger in lookback; filter must be no-op)")
    print(f"  N = {ctrl['n']}")
    print(f"  TP  (correct passthrough):   {ctrl['tp']}")
    print(f"  Filter over-fired (regex):   {ctrl['filter_overfire']}")
    print(f"  Presidio missed:             {ctrl['presidio_fn']}")
    print(f"  False-fire rate:             {rates['false_fire']:.1%}   target = 0%")

    print("\n=== Headline ===")
    print(f"  Over-suppression rate (Cat 1): {rates['over_suppression']:.1%}")
    print(f"  Suppression precision (Cat 2): {rates['suppression_precision']:.1%}")
    print(f"  False-fire rate       (Cat 3): {rates['false_fire']:.1%}")


def _aggregate(truth: list[dict], threshold: float, apply_filter: bool = False) -> dict:
    """Run eval across all files at one threshold; return aggregate metrics only."""
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for entry in truth:
        res = evaluate_file(
            entry["source"], entry["spans"], threshold=threshold, apply_filter=apply_filter
        )
        totals["tp"] += res["tp"]
        totals["fp"] += res["fp"]
        totals["fn"] += res["fn"]
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f_beta(precision, recall, beta=1.0),
        "f2": f_beta(precision, recall, beta=2.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="PII detection evaluation")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help=(
            "Presidio confidence threshold (0.0=raw recognizer, prod uses "
            "settings.redaction_confidence_threshold)"
        ),
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help=f"Run at multiple thresholds {SWEEP_THRESHOLDS} and print PR comparison table",
    )
    parser.add_argument(
        "--filter",
        action="store_true",
        help="Apply ID-context post-filter to suppress findings preceded by ID-suggesting tokens",
    )
    parser.add_argument(
        "--holdout",
        action="store_true",
        help=(
            "Run the held-out filter eval (ISS-005). Sentence-level 2x2 + per-category "
            "over-suppression / suppression-precision / false-fire rates. Filter is always "
            "applied — filter behavior is what's being measured."
        ),
    )
    args = parser.parse_args()

    if args.holdout:
        result = run_holdout_eval(threshold=args.threshold)
        _print_holdout_report(result)
        return

    truth = load_ground_truth()
    if not truth:
        print("No reviewed ground truth entries. Mark entries as reviewed=true first.")
        return

    if args.sweep:
        filter_label = "ON" if args.filter else "OFF"
        print(
            f"=== PII Detection Eval — Threshold Sweep "
            f"(filter={filter_label}, {len(truth)} files) ===\n"
        )
        header = (
            f"{'threshold':>10} {'TP':>4} {'FP':>4} {'FN':>4} "
            f"{'Prec':>7} {'Recall':>7} {'F1':>7} {'F2':>7}"
        )
        print(header)
        print("-" * len(header))
        for t in SWEEP_THRESHOLDS:
            m = _aggregate(truth, t, apply_filter=args.filter)
            print(
                f"{t:>10.2f} {m['tp']:>4} {m['fp']:>4} {m['fn']:>4} "
                f"{m['precision']:>7.3f} {m['recall']:>7.3f} {m['f1']:>7.3f} {m['f2']:>7.3f}"
            )
        return

    filter_label = "ON" if args.filter else "OFF"
    print(
        f"=== PII Detection Evaluation "
        f"(threshold={args.threshold}, filter={filter_label}, {len(truth)} reviewed files) ===\n"
    )

    # Per-file breakdown
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for entry in truth:
        result = evaluate_file(
            entry["source"],
            entry["spans"],
            threshold=args.threshold,
            apply_filter=args.filter,
        )
        totals["tp"] += result["tp"]
        totals["fp"] += result["fp"]
        totals["fn"] += result["fn"]
        print(
            f"  {entry['source']}: "
            f"TP={result['tp']} FP={result['fp']} FN={result['fn']} "
            f"(found {result['findings']}, expected {result['expected']})"
        )

    # Aggregate metrics
    tp, fp, fn = totals["tp"], totals["fp"], totals["fn"]
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = f_beta(precision, recall, beta=1.0)
    f2 = f_beta(precision, recall, beta=2.0)

    print("\n=== Aggregate ===")
    print(f"  TP={tp}  FP={fp}  FN={fn}")
    print(f"  Precision: {precision:.3f}  (of what we flagged, fraction that was real PII)")
    print(f"  Recall:    {recall:.3f}  (of real PII, fraction we caught)")
    print(f"  F1:        {f1:.3f}  (balanced)")
    print(f"  F2:        {f2:.3f}  (recall-weighted, primary PII metric)")


if __name__ == "__main__":
    main()
