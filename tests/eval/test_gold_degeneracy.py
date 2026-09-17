"""Tests for gold degeneracy detection in the SQL eval harness."""

from scripts.eval.evaluate_sql_agent import assess_gold_degeneracy


def test_verify_gold_flags_all_null_gold_as_candidate():
    candidate, reason = assess_gold_degeneracy([(None,)])
    assert candidate is True
    assert reason == "all_null_gold"


def test_verify_gold_does_not_flag_legit_zero_result():
    """A real zero count (0,) is scored until a human confirms degeneracy."""
    candidate, reason = assess_gold_degeneracy([(0,)])
    assert candidate is False
    assert reason is None


def test_assess_gold_flags_empty_rows():
    candidate, reason = assess_gold_degeneracy([])
    assert candidate is True
    assert reason == "empty_gold"


def test_analyze_warns_on_unconfirmed_candidate(capsys):
    from scripts.eval.analyze_sql_runs import summarize

    summary = {
        "git_sha": "abc",
        "model": "test",
        "n_repeats": 1,
        "valid_sql_rate": 1.0,
        "mean_sql_attempts": 1.0,
        "p50_latency_ms": 100,
        "p95_latency_ms": 200,
        "infra_errors": 0,
        "flaky_cases": [],
        "safety": {},
        "cost": {},
        "degenerate_candidates": ["new-degenerate-case"],
        "cases": [
            {
                "case_id": "new-degenerate-case",
                "pass_rate": 1.0,
                "evaluated_runs": 1,
                "degenerate_candidate": True,
                "degenerate_reason": "empty_gold",
            },
            {
                "case_id": "real-case",
                "pass_rate": 0.5,
                "evaluated_runs": 1,
                "degenerate_candidate": False,
                "degenerate_reason": None,
            },
        ],
    }
    summarize(summary, tag="test")
    err = capsys.readouterr().err
    assert "new degenerate candidate" in err
    assert "new-degenerate-case" in err
