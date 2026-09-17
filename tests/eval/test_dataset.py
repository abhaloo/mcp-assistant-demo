"""The eval-case definition exists ONCE (review M6)."""

from app.eval.dataset import EXCLUDE_PREFIXES, load_eval_cases


def test_semantic_excludes_nonquality_cases():
    cases = load_eval_cases("semantic")
    assert cases, "dataset must load"
    assert all(not c["id"].startswith(EXCLUDE_PREFIXES) for c in cases)
    assert all(c.get("expected_answer") for c in cases)


def test_structured_is_sql_prefixed():
    assert all(c["id"].startswith("sql-") for c in load_eval_cases("structured"))


def test_subset_filter():
    all_sem = load_eval_cases("semantic")
    one = load_eval_cases("semantic", subset=[all_sem[0]["id"]])
    assert [c["id"] for c in one] == [all_sem[0]["id"]]
