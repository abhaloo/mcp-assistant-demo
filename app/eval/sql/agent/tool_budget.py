import re
from dataclasses import dataclass

SQL_RESULT_TRUNCATION_MARKER = (
    "Result truncated; refine the query (add filters/aggregates) or ask for a smaller slice."
)

# Every non-empty result opens with this. Without it the payload is a column header
# and rows, indistinguishable from text a user pasted -- which is how Luna came to
# treat its own output as user input and re-derive the same query 27 times.
SQL_RESULT_HEADER_PREFIX = "[sql_db_query result:"

LIMIT_N_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_LIMIT_RE = LIMIT_N_RE


@dataclass(frozen=True)
class SqlToolOutputBudget:
    max_rows: int
    max_chars: int


def _provenance_header(
    *,
    row_count: int,
    truncated: bool,
    total_rows: int | None,
    submitted_sql: str | None,
) -> str:
    """State what this payload is, how much of the result it holds, and why.

    ``total_rows`` is reported only when the caller actually counted; a total is
    never inferred, because a wrong number is worse than no number.
    """
    if truncated and total_rows is not None:
        counted = f"{row_count} of {total_rows} rows returned"
    else:
        counted = f"{row_count} rows returned"

    parts = [counted]
    if truncated:
        parts.append("result truncated at the row cap")
    match = _LIMIT_RE.search(submitted_sql or "")
    if match:
        parts.append(f"query included LIMIT {match.group(1)}")
    return f"{SQL_RESULT_HEADER_PREFIX} {'; '.join(parts)}]"


def _format_with_headers(columns: list[str], value_rows: list[tuple]) -> str:
    header = "\t".join(columns)
    if not value_rows:
        return header
    body = "\n".join("\t".join("" if v is None else str(v) for v in row) for row in value_rows)
    return header + "\n" + body


def format_sql_tool_result(
    rows: list[tuple] | list[dict],
    *,
    budget: SqlToolOutputBudget,
    truncated_by_row_cap: bool,
    columns: list[str] | None = None,
    submitted_sql: str | None = None,
    total_rows: int | None = None,
) -> str:
    if not rows:
        return ""

    if rows and isinstance(rows[0], dict):
        dict_rows: list[dict] = list(rows)  # type: ignore[arg-type]
        cols = columns or list(dict_rows[0].keys())
        value_rows = [tuple(r.get(c) for c in cols) for r in dict_rows]
        text = _format_with_headers(cols, value_rows)
    elif columns is not None:
        value_rows = [tuple(r) for r in rows]  # type: ignore[arg-type]
        text = _format_with_headers(list(columns), value_rows)
    else:
        text = str(rows)

    truncated = truncated_by_row_cap

    if len(text) > budget.max_chars:
        text = text[: budget.max_chars]
        truncated = True

    header = _provenance_header(
        row_count=len(rows),
        truncated=truncated,
        total_rows=total_rows,
        submitted_sql=submitted_sql,
    )
    text = header + "\n" + text

    if truncated:
        return text + "\n" + SQL_RESULT_TRUNCATION_MARKER

    return text
