from app.eval.sql.agent.tool_budget import (
    SQL_RESULT_HEADER_PREFIX,
    SQL_RESULT_TRUNCATION_MARKER,
    SqlToolOutputBudget,
    format_sql_tool_result,
)


def test_row_cap_appends_explicit_marker():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    rows = [(i, f"job-{i}") for i in range(20)]
    out = format_sql_tool_result(rows, budget=budget, truncated_by_row_cap=True)
    assert out.count("job-") == 20
    assert SQL_RESULT_TRUNCATION_MARKER in out
    assert not out.startswith("Error:")


def test_size_cap_truncates_huge_cell():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=200)
    rows = [(1, "x" * 10_000)]
    out = format_sql_tool_result(rows, budget=budget, truncated_by_row_cap=False)
    marker = SQL_RESULT_TRUNCATION_MARKER
    assert marker in out
    body, sep, _tail = out.partition("\n" + marker)
    assert sep  # marker appended after newline
    # Widened for the provenance header: max_chars bounds the DATA, which is what
    # the cap is for. The header is a fixed-size label, not payload.
    header, _, data = body.partition("\n")
    assert header.startswith(SQL_RESULT_HEADER_PREFIX)
    assert len(data) <= budget.max_chars


def test_aggregate_exact_no_marker():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    out = format_sql_tool_result([(328,)], budget=budget, truncated_by_row_cap=False)
    header, _, data = out.partition("\n")
    assert header.startswith(SQL_RESULT_HEADER_PREFIX)
    assert data == "[(328,)]"
    assert SQL_RESULT_TRUNCATION_MARKER not in out


def test_empty_result_is_empty_string():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    assert format_sql_tool_result([], budget=budget, truncated_by_row_cap=False) == ""


# --- provenance header ---------------------------------------------------------------
# Luna described its own query results as user input on 8 verbatim reasoning steps
# ("the user pasted something repetitive", "the lists provided") and re-derived from
# that premise 27 times. The payload carried a column header and rows and nothing
# saying what it was. The old truncation marker could not help: it only fires when the
# DB has more rows than the cap, and Luna wrote its own LIMIT 20, so 20 > 20 was False.


_BUDGET = SqlToolOutputBudget(max_rows=20, max_chars=12_000)


def _header_line(text: str) -> str:
    return text.splitlines()[0]


def test_untruncated_result_still_gets_a_provenance_header():
    """The whole point: fires on EVERY result, not only the rare row-cap trip."""
    out = format_sql_tool_result(
        [{"id": 1}, {"id": 2}, {"id": 3}],
        budget=_BUDGET,
        truncated_by_row_cap=False,
        columns=["id"],
        submitted_sql="SELECT id FROM c",
    )
    head = _header_line(out)
    assert head.startswith(SQL_RESULT_HEADER_PREFIX)
    assert "3 rows returned" in head


def test_header_echoes_a_limit_the_model_wrote_itself():
    """The cust-no-orders path. The old marker could never fire here."""
    out = format_sql_tool_result(
        [{"id": i} for i in range(20)],
        budget=_BUDGET,
        truncated_by_row_cap=False,
        columns=["id"],
        submitted_sql="SELECT id FROM c LEFT JOIN o ON o.c=c.id WHERE o.id IS NULL LIMIT 20",
    )
    head = _header_line(out)
    assert "20 rows returned" in head
    assert "LIMIT 20" in head


def test_header_reports_the_total_when_the_row_cap_tripped():
    out = format_sql_tool_result(
        [{"id": i} for i in range(20)],
        budget=_BUDGET,
        truncated_by_row_cap=True,
        columns=["id"],
        submitted_sql="SELECT id FROM c",
        total_rows=597,
    )
    head = _header_line(out)
    assert "20 of 597 rows returned" in head


def test_header_never_fabricates_a_total_it_does_not_have():
    out = format_sql_tool_result(
        [{"id": i} for i in range(20)],
        budget=_BUDGET,
        truncated_by_row_cap=True,
        columns=["id"],
        submitted_sql="SELECT id FROM c",
        total_rows=None,
    )
    head = _header_line(out)
    assert "20 rows returned" in head
    assert " of " not in head


def test_empty_result_is_still_the_empty_string():
    out = format_sql_tool_result([], budget=_BUDGET, truncated_by_row_cap=False)
    assert out == ""


def test_column_header_and_rows_survive_beneath_the_provenance_line():
    out = format_sql_tool_result(
        [{"id": 1, "name": "a"}],
        budget=_BUDGET,
        truncated_by_row_cap=False,
        columns=["id", "name"],
        submitted_sql="SELECT id, name FROM c",
    )
    lines = out.splitlines()
    assert lines[1] == "id\tname"
    assert lines[2] == "1\ta"


def test_shape_parser_row_count_is_unaffected_by_the_header():
    """The header must not shift row_count -- the shape gate reads this payload.

    parse_sql_tool_result counts len(lines) - 1 assuming line 0 is the column
    header. An unstripped provenance line makes every count off by one and the
    shape gate misreads results silently.
    """
    from app.eval.sql.agent.shape_validate import parse_sql_tool_result

    out = format_sql_tool_result(
        [{"id": 1}, {"id": 2}, {"id": 3}],
        budget=_BUDGET,
        truncated_by_row_cap=False,
        columns=["id"],
        submitted_sql="SELECT id FROM c",
    )
    parsed = parse_sql_tool_result(out)
    assert parsed.outcome == "rows"
    assert parsed.row_count == 3


def test_shape_parser_handles_header_and_truncation_marker_together():
    from app.eval.sql.agent.shape_validate import parse_sql_tool_result

    out = format_sql_tool_result(
        [{"id": i} for i in range(20)],
        budget=_BUDGET,
        truncated_by_row_cap=True,
        columns=["id"],
        submitted_sql="SELECT id FROM c",
        total_rows=597,
    )
    parsed = parse_sql_tool_result(out)
    assert parsed.outcome == "rows"
    assert parsed.row_count == 20
