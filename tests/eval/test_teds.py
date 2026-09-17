from app.eval.parsing.teds import TEDS

FULL = "<html><body>{}</body></html>"
T1 = FULL.format("<table><tr><td>a</td><td>b</td></tr></table>")
T2 = FULL.format("<table><tr><td>a</td><td>b</td></tr></table>")
T3 = FULL.format("<table><tr><td>a</td></tr><tr><td>b</td></tr></table>")  # 1 col, 2 rows


def test_identical_tables_score_one():
    assert TEDS().evaluate(T1, T2) == 1.0


def test_different_structure_scores_below_one():
    score = TEDS(structure_only=True).evaluate(T1, T3)
    assert 0.0 < score < 1.0


def test_empty_prediction_scores_zero():
    assert TEDS().evaluate("", T1) == 0.0
