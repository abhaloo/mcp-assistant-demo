"""Parse-fidelity scoring + the parser-swap gate.

teds_score        — table structure fidelity (wraps app/eval/parsing/teds.py)
reading_order_score — sequence similarity of block order (1 - normalized Levenshtein)
"""

from __future__ import annotations

_HTML = "<html><body>{}</body></html>"


def teds_score(pred_html: str | None, true_html: str | None, structure_only: bool = False) -> float:
    """TEDS between two table HTML fragments. Bare <table> fragments are wrapped
    in <html><body> so the underlying TEDS xpath matches. Returns 0.0 if either
    side is missing."""
    if not pred_html or not true_html:
        return 0.0
    from app.eval.parsing.teds import TEDS

    pred = pred_html if "<body" in pred_html else _HTML.format(pred_html)
    true = true_html if "<body" in true_html else _HTML.format(true_html)
    return TEDS(structure_only=structure_only).evaluate(pred, true)


def reading_order_score(predicted: list[str], expected: list[str]) -> float:
    """1 - normalized Levenshtein over two sequences of block labels.
    1.0 means identical order; lower means blocks were reordered/missing."""
    import distance

    if not predicted and not expected:
        return 1.0
    denom = max(len(predicted), len(expected)) or 1
    return 1.0 - (distance.levenshtein(predicted, expected) / denom)


def evaluate_parse(
    predicted_tables: list[str],
    expected_tables: list[str],
    predicted_order: list[str],
    expected_order: list[str],
) -> dict:
    """Score one document's parse against ground truth.

    Tables are matched positionally (table i vs expected table i); a missing
    predicted table scores 0.0 for that slot. mean_teds is the average across
    all expected tables, so under-extraction is penalized. Extra or reordered
    tables in the prediction are not aligned — revisit once real fixtures exist.
    """
    teds_scores: list[float] = []
    for i, exp in enumerate(expected_tables):
        pred = predicted_tables[i] if i < len(predicted_tables) else None
        teds_scores.append(teds_score(pred, exp))
    mean_teds = sum(teds_scores) / len(teds_scores) if teds_scores else 0.0
    return {
        "mean_teds": mean_teds,
        "per_table_teds": teds_scores,
        "reading_order": reading_order_score(predicted_order, expected_order),
        "n_expected_tables": len(expected_tables),
        "n_predicted_tables": len(predicted_tables),
    }


# RAGAS metrics where a DROP is bad (higher = better).
_RAGAS_KEYS = ("faithfulness", "context_precision")


def decide_gate(baseline: dict, candidate: dict, max_ragas_regression: float = 0.02) -> dict:
    """Pre-committed parser-swap gate. Adopt the candidate parser over the
    baseline ONLY IF it does not regress any RAGAS metric beyond the tolerance
    AND it improves parse fidelity (mean_teds). Answer quality wins ties — a
    prettier parse that doesn't help (or hurts) answers is not adopted.
    """
    for key in _RAGAS_KEYS:
        base = baseline.get(key)
        cand = candidate.get(key)
        if base is None or cand is None:
            continue
        if (base - cand) > max_ragas_regression:
            return {
                "adopt": False,
                "reason": f"{key} regressed {base - cand:.3f} > {max_ragas_regression}",
            }
    if candidate.get("mean_teds", 0.0) <= baseline.get("mean_teds", 0.0):
        return {"adopt": False, "reason": "no parse-fidelity (mean_teds) improvement"}
    return {
        "adopt": True,
        "reason": "RAGAS within tolerance and mean_teds improved",
    }


def load_manifest(path: str) -> list[dict]:
    """Read a JSONL ground-truth manifest into a list of dict rows.
    One JSON object per line; blank lines ignored."""
    import json

    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
