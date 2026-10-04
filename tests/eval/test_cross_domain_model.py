"""Model-arm classification for the cross-domain benchmark (no model call in tests)."""

from __future__ import annotations

from app.eval.business_query.cross_domain_model import classify_model_outcome


def test_model_outcomes_map_onto_the_shared_table() -> None:
    """Oracle: design section 2 model-arm gate — chose-right-plan / valid / matches /
    honest unsupported / clarify / stall."""
    assert classify_model_outcome(expected="answer", outcome_kind="answered", matched=True) == (
        "pass",
        "planned_match",
    )
    assert classify_model_outcome(expected="answer", outcome_kind="answered", matched=False) == (
        "wrong_answer",
        "planned_wrong",
    )
    assert classify_model_outcome(expected="answer", outcome_kind="unsupported", matched=None) == (
        "engine_rule",
        "honest_unsupported",
    )
    assert classify_model_outcome(expected="answer", outcome_kind="clarify", matched=None) == (
        "engine_rule",
        "clarify",
    )
    assert classify_model_outcome(expected="answer", outcome_kind="incomplete", matched=None) == (
        "no_plan",
        "stall",
    )
    assert classify_model_outcome(
        expected="capability_gap", outcome_kind="unsupported", matched=None
    ) == ("capability_gap", "honest_unsupported")
