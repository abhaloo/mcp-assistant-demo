"""SQLDatabase wrapper with reversible PII tokenization for the legacy SQL agent."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from langchain_community.utilities import SQLDatabase
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from app.guardrails.pii_register import (
    REGISTER_TABLES,
    suppress_columns,
)
from app.guardrails.sql_anonymizer import (
    SuppressedColumnError,
)

if TYPE_CHECKING:
    from app.eval.sql.agent.anonymizer import SqlAnonymizer
    from app.eval.sql.agent.tool_budget import SqlToolOutputBudget

_TABLE_RE = re.compile(r"\b(?:from|join)\s+[`\"\[]?(\w+)", re.IGNORECASE)


def _tables_in(sql: str) -> set[str]:
    return {m.group(1).lower() for m in _TABLE_RE.finditer(sql)}


def _count_total_rows(connection, sql: str, params: dict[str, object] | None) -> int | None:
    """How many rows the model's own query would return, for the provenance header.

    Runs only when the row cap tripped, on the same connection with the same bind
    parameters, so row-level scope cannot be widened by the count. Any failure
    (timeout, a dialect that rejects the subquery) yields no total: a wrong number
    is worse than none, and the header omits what it cannot verify. The scalar
    never reaches ``record_result_rows`` and is never tokenized.
    """
    inner = sql.rstrip().rstrip(";")
    try:
        wrapped = text(f"SELECT COUNT(*) FROM ({inner}) _provenance_count")
        return int(connection.execute(wrapped, params or {}).scalar_one())
    except Exception:  # noqa: BLE001 — header degrades, query result still returns
        return None


def _tables_from_generate_query(update: dict) -> list[str]:
    """Table names from generated SQL (tool_calls only — message content is prose)."""
    names: set[str] = set()
    for message in update.get("messages", []):
        for call in getattr(message, "tool_calls", None) or []:
            query = (call.get("args") or {}).get("query")
            if query:
                names |= _tables_in(str(query))
    return sorted(names)


def _scrub_suppressed_schema_lines(info: str, tables: set[str]) -> str:
    suppressed = suppress_columns(tables or set(REGISTER_TABLES))
    if not suppressed:
        return info
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(col) for col in sorted(suppressed)) + r")\b",
        re.IGNORECASE,
    )
    return "\n".join(line for line in info.splitlines() if not pattern.search(line))


class AnonymizingSQLDatabase(SQLDatabase):
    """
    SQLDatabase subclass that round-trips PII through a SqlAnonymizer.

    Why subclass instead of compose? LangChain's SQLDatabaseToolkit
    declares `db: SQLDatabase` with a Pydantic `is_instance_of` validator
    — a composition Decorator (even with __getattr__ proxying every call)
    fails that check because it's not in the inheritance chain. Subclassing
    inherits the type identity for free.

    Intercept at _execute: run()/run_no_throw() both funnel here.
    Deanonymize agent-injected tokens in the SQL (so WHERE matches real
    values), execute, then tokenize result rows column-aware BEFORE the
    parent stringifies them — this is the B1 'hard guarantee' wiring.

    Pass `anonymizer=None` to get a transparent SQLDatabase — useful when
    a single construction site needs to handle both wrapped and raw cases.
    """

    def __init__(self, *args, anonymizer: SqlAnonymizer | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._anonymizer = anonymizer

    def _execute(self, command, fetch: str = "all", **kwargs):
        """Single interception point: run()/run_no_throw() both funnel here."""
        if self._anonymizer is None:
            return super()._execute(command, fetch=fetch, **kwargs)
        real = self._anonymizer.deanonymize(str(command))
        self._reject_suppressed(real)
        rows = super()._execute(real, fetch=fetch, **kwargs)
        if not isinstance(rows, list):
            return rows
        self._anonymizer.record_result_rows(real, rows)
        return self._anonymizer.anonymize_rows(rows, _tables_in(real))

    def run_bounded(
        self,
        command: str,
        *,
        budget: SqlToolOutputBudget,
        params: dict[str, object] | None = None,
    ) -> str:
        """Execute read query; fetch at most max_rows+1; anonymize; serialize with caps.

        ``params`` are SQLAlchemy bind values for named placeholders (S1 row RLS).
        Never interpolate them into the SQL string.
        """
        from app.eval.sql.agent.tool_budget import format_sql_tool_result

        real = str(command)
        if self._anonymizer is not None:
            real = self._anonymizer.deanonymize(real)
            self._reject_suppressed(real)

        total_rows: int | None = None
        with self._engine.connect() as connection:
            cursor = connection.execute(text(real), params or {})
            if not cursor.returns_rows:
                return ""
            raw = cursor.fetchmany(budget.max_rows + 1)

            truncated_by_row_cap = len(raw) > budget.max_rows
            if truncated_by_row_cap:
                raw = raw[: budget.max_rows]
                total_rows = _count_total_rows(connection, real, params)

        rows = [row._asdict() for row in raw]
        if self._anonymizer is not None:
            self._anonymizer.record_result_rows(real, rows)
            rows = self._anonymizer.anonymize_rows(rows, _tables_in(real))

        columns = list(rows[0].keys()) if rows else None
        return format_sql_tool_result(
            rows,
            budget=budget,
            truncated_by_row_cap=truncated_by_row_cap,
            columns=columns,
            submitted_sql=str(command),
            total_rows=total_rows,
        )

    def _reject_suppressed(self, sql: str) -> None:
        """Defense-in-depth: reject SQL naming a suppress column among referenced
        tables. Evadable (aliases/CONCAT/views) — the boundary is a DB-level
        least-privilege view (see ADR 0027). SELECT * doesn't name the column, so
        it passes here and is caught by row masking."""
        tables = _tables_in(sql) or set(REGISTER_TABLES)
        for col in suppress_columns(tables):
            if re.search(rf"\b{re.escape(col)}\b", sql, re.IGNORECASE):
                raise SuppressedColumnError(
                    "Query references a restricted column and was blocked by policy."
                )

    def _get_sample_rows(self, table) -> str:
        """Route sample rows through the column-aware anonymizer so redact/
        suppress sample values never reach the LLM via schema info. The parent
        fetches sample rows via self._engine.connect() directly, bypassing our
        _execute, so we reimplement its format here."""
        if self._anonymizer is None:
            return super()._get_sample_rows(table)
        suppressed = suppress_columns({table.name})
        visible_columns = [col for col in table.columns if col.name.lower() not in suppressed]
        col_names = [col.name for col in visible_columns]
        columns_str = "\t".join(col_names)
        header = f"{self._sample_rows_in_table_info} rows from {table.name} table:\n{columns_str}\n"
        if not visible_columns:
            return header
        command = select(*visible_columns).limit(self._sample_rows_in_table_info)
        try:
            with self._engine.connect() as connection:
                result = connection.execute(command)
                raw_rows = [dict(zip(col_names, row, strict=True)) for row in result]
        except ProgrammingError:
            return header
        anon_rows = self._anonymizer.anonymize_rows(raw_rows, {table.name})
        body = "\n".join(
            "\t".join(str(anon_row.get(c, ""))[:100] for c in col_names) for anon_row in anon_rows
        )
        return header + body

    def get_table_info(self, *args, **kwargs):
        info = super().get_table_info(*args, **kwargs)
        if self._anonymizer is None:
            return info
        info = _scrub_suppressed_schema_lines(info, set(REGISTER_TABLES))
        return self._anonymizer.anonymize(info, source="sql_schema")
