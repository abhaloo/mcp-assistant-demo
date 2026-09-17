from decimal import Decimal

from app.eval.sql.exec_match import compare, order_matters


def test_order_matters_detects_order_by():
    assert order_matters("SELECT x FROM t ORDER BY x") is True
    assert order_matters("select * from t") is False


def test_bag_equal_ignores_row_order_without_order_by():
    assert compare([(2,), (1,), (2,)], [(1,), (2,), (2,)], "SELECT n FROM t")["match"] is True


def test_duplicates_count_bag_not_set():
    assert compare([(2,)], [(2,), (2,)], "SELECT n FROM t")["match"] is False


def test_order_enforced_with_order_by():
    assert compare([(2,), (1,)], [(1,), (2,)], "SELECT n FROM t ORDER BY n")["match"] is False


def test_float_decimal_equal():
    assert compare([(12.1,)], [(Decimal("12.10"),)], "SELECT total FROM t")["match"] is True


def test_null_handling():
    assert compare([(None, 1)], [(None, 1)], "SELECT a, b FROM t")["match"] is True


def test_row_count_mismatch_reason():
    assert compare([(1,)], [(1,), (2,)], "SELECT n FROM t")["reason"] == "row_count_mismatch"


def test_column_permutation_aligns():
    assert compare([(3, "Ali")], [("Ali", 3)], "SELECT name, c FROM t")["match"] is True


def test_no_agent_query():
    assert compare(None, [(1,)], "SELECT n FROM t") == {"match": False, "reason": "no_agent_query"}


def test_empty_sets_equal():
    assert compare([], [], "SELECT n FROM t WHERE 1=0")["match"] is True


def test_money_rounding_artifact_matches():
    # Gold ROUND(...,0) discards the cents the agent keeps — same business answer.
    assert compare([(5640231.47,)], [(5640231,)], "SELECT total FROM t")["match"] is True


def test_money_half_unit_is_the_ceiling():
    # ROUND-to-0 can only shift a value by <=0.5; a 0.6 gap is a real difference.
    assert compare([(100.0,)], [(100.4,)], "SELECT total FROM t")["match"] is True
    assert compare([(100.0,)], [(100.6,)], "SELECT total FROM t")["match"] is False


def test_count_off_by_one_still_fails():
    # Integer-valued cells (row counts) compare exactly — the tolerance must not forgive them.
    r = compare([(329,)], [(328,)], "SELECT COUNT(*) FROM t")
    assert r["match"] is False and r["reason"] == "value_mismatch"


def test_large_money_error_still_fails():
    # The "tax off the wrong table" class moves totals by far more than the floor.
    assert compare([(5700000.5,)], [(5640231,)], "SELECT total FROM t")["match"] is False


def test_superset_columns_match_when_gold_subset_present():
    # agent returns (id, name); gold wants (name,); 1 row, no ORDER BY
    r = compare([(7, "Acme")], [("Acme",)], "select name ...")
    assert r["match"] and r["reason"] == "superset_equal"


def test_superset_does_not_mask_wrong_value():
    r = compare([(7, "Beta")], [("Acme",)], "select name ...")
    assert not r["match"]


def test_superset_does_not_forgive_extra_rows():
    r = compare([("Acme",), ("Beta",)], [("Acme",)], "select name ...")
    assert not r["match"] and r["reason"] == "row_count_mismatch"
