from app.eval.parsing.metrics import reading_order_score, teds_score


def test_teds_score_wraps_bare_table_fragments():
    # Callers pass bare <table> fragments (what Unstructured returns); the
    # wrapper adds <html><body> so TEDS' body/table xpath matches.
    bare = "<table><tr><td>a</td></tr></table>"
    assert teds_score(bare, bare) == 1.0


def test_teds_score_handles_none():
    assert teds_score(None, "<table><tr><td>a</td></tr></table>") == 0.0


def test_reading_order_identical_is_one():
    seq = ["Title", "Body", "Total"]
    assert reading_order_score(seq, seq) == 1.0


def test_reading_order_reversed_below_one():
    score = reading_order_score(["a", "b", "c"], ["c", "b", "a"])
    assert 0.0 <= score < 1.0
