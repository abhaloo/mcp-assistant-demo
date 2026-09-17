import importlib

import pytest

mod = importlib.import_module("scripts.experiments.analyze_escalated")


def _run_summary(case_ids: list[str], runs: list[dict]) -> dict:
    return {
        "git_sha": "test",
        "model": "m",
        "n_repeats": 5,
        "escalated_cases": case_ids,
        "cases": [{"case_id": cid, "pass_rate": 1.0, "evaluated_runs": 5} for cid in case_ids],
        "runs": runs,
    }


def test_invalid_sql_rate_counts_reason_only():
    ids = {"a", "b"}
    summary = _run_summary(
        ["a", "b"],
        [
            {"case_id": "a", "reason": "invalid_sql"},
            {"case_id": "b", "reason": "no_query"},
            {"case_id": "a", "reason": "ok", "infra_error": True},
        ],
    )
    assert mod._invalid_sql_rate(summary, ids) == 0.5


def test_invalid_sql_rate_empty_returns_zero():
    summary = _run_summary([], [])
    assert mod._invalid_sql_rate(summary, set()) == 0.0


def test_cost_rollup_raises_on_unknown_deployment():
    runs = [{"case_id": "x", "chat_deployment": "unknown-model", "tokens_prompt": 100}]
    with pytest.raises(KeyError, match="No PRICES entry"):
        mod._cost_rollup(runs)


def test_cost_rollup_luna_pricing():
    runs = [
        {
            "case_id": "x",
            "chat_deployment": "gpt-5.6-luna",
            "tokens_prompt": 1_000_000,
            "tokens_completion": 0,
            "tokens_cached": 0,
        }
    ]
    by_model = mod._cost_rollup(runs)
    assert by_model["gpt-5.6-luna"]["usd"] == pytest.approx(0.20)
