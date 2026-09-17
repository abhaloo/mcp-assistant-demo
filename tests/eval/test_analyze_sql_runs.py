import importlib

mod = importlib.import_module("scripts.eval.analyze_sql_runs")


def _summary(rates: dict[str, float]) -> dict:
    return {
        "git_sha": "test",
        "model": "m",
        "n_repeats": 5,
        "cases": [{"case_id": k, "pass_rate": v, "evaluated_runs": 5} for k, v in rates.items()],
        "safety": {},
        "cost": {},
    }


def test_compare_reports_verdict_and_regression(capsys):
    base = _summary({"x1": 1.0, "x2": 1.0, "x3": 0.0, "x4": 0.0, "x5": 0.8})
    # 1 regression, 2 improvements, x5 sub-threshold
    cand = _summary({"x1": 0.0, "x2": 1.0, "x3": 1.0, "x4": 1.0, "x5": 1.0})
    mod.compare(base, cand, "base", "cand")
    out = capsys.readouterr().out
    assert "VERDICT" in out
    assert "regressions" in out.lower()
    assert "x1" in out  # the regressed case is named
    assert "sub-threshold" in out and "x5" in out  # within-band move surfaced
