"""Header-aware format_sql_tool_result (V4)."""

from app.eval.sql.agent.tool_budget import (
    SQL_RESULT_HEADER_PREFIX,
    SQL_RESULT_TRUNCATION_MARKER,
    SqlToolOutputBudget,
    format_sql_tool_result,
)


def _column_header(out: str) -> str:
    """Line 0 is the provenance line; the column header is the one after it."""
    lines = out.splitlines()
    assert lines[0].startswith(SQL_RESULT_HEADER_PREFIX)
    return lines[1]


def test_dict_rows_include_column_headers():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    rows = [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]
    out = format_sql_tool_result(rows, budget=budget, truncated_by_row_cap=False)
    header = _column_header(out)
    assert "id" in header
    assert "name" in header
    assert "alice" in out
    assert SQL_RESULT_TRUNCATION_MARKER not in out


def test_tuple_rows_with_columns_kwarg_include_headers():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    out = format_sql_tool_result(
        [(1, "alice")],
        budget=budget,
        truncated_by_row_cap=False,
        columns=["id", "name"],
    )
    header = _column_header(out)
    assert "id" in header and "name" in header


def test_tuple_rows_without_columns_keep_legacy_repr():
    """Budgets regression: the bare-tuple BODY stays byte-compatible.

    Widened for the provenance line, which is a label above the body rather than a
    change to how bare tuples serialize.
    """
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    out = format_sql_tool_result([(328,)], budget=budget, truncated_by_row_cap=False)
    header, _, data = out.partition("\n")
    assert header.startswith(SQL_RESULT_HEADER_PREFIX)
    assert data == "[(328,)]"


def test_header_path_still_appends_truncation_marker():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    rows = [{"id": i, "name": f"row-{i}"} for i in range(5)]
    out = format_sql_tool_result(rows, budget=budget, truncated_by_row_cap=True)
    assert SQL_RESULT_TRUNCATION_MARKER in out
