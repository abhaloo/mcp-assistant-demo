"""Two-primary gate decision for chunking experiments (ADR 0015)."""


def evaluate_gate(
    det_scores: dict,
    baseline: dict | None,
    threshold: float,
    regression_tolerance: float = -0.05,
    saturation_floor: float = 0.99,
) -> tuple[bool, dict]:
    """Two-primary gate decision: should this config proceed to RAGAS?"""
    if baseline is None:
        return True, {"reason": "no baseline; advancing unconditionally"}

    b_recall = baseline.get("recall_at_Ntok")
    b_snippet = baseline.get("snippet_hit_at_Ntok")
    if b_recall is None and b_snippet is None:
        return True, {"reason": "baseline has no primary scores; gate skipped"}

    d_recall = (det_scores.get("recall_at_Ntok") - b_recall) if b_recall is not None else None
    d_snippet = (
        (det_scores.get("snippet_hit_at_Ntok") - b_snippet) if b_snippet is not None else None
    )

    recall_saturated = b_recall is not None and b_recall >= saturation_floor
    snippet_saturated = b_snippet is not None and b_snippet >= saturation_floor

    recall_improves = d_recall is not None and (
        (recall_saturated and d_recall >= 0) or (not recall_saturated and d_recall >= threshold)
    )
    snippet_improves = d_snippet is not None and (
        (snippet_saturated and d_snippet >= 0) or (not snippet_saturated and d_snippet >= threshold)
    )
    has_improvement = recall_improves or snippet_improves

    no_hard_regression = (d_recall is None or d_recall >= regression_tolerance) and (
        d_snippet is None or d_snippet >= regression_tolerance
    )

    advanced = has_improvement and no_hard_regression
    return advanced, {
        "b_recall": b_recall,
        "b_snippet": b_snippet,
        "d_recall": d_recall,
        "d_snippet": d_snippet,
        "recall_saturated": recall_saturated,
        "snippet_saturated": snippet_saturated,
        "recall_improves": recall_improves,
        "snippet_improves": snippet_improves,
        "has_improvement": has_improvement,
        "no_hard_regression": no_hard_regression,
    }
