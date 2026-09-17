"""Unit tests for sql_dry_run EXPLAIN validate (Slice C)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.eval.sql.agent.anonymizing_database import AnonymizingSQLDatabase
from app.eval.sql.agent.dry_run import (
    DRY_RUN_PREFIX,
    _assert_sanitized_reason,
    _stable_reason_from_exception,
    explain_validate,
    run_explain,
)
from app.eval.sql.agent.graph import _validate_sql_query
from app.tools.types import ToolRejection


def _sqlite_db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'dry.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        c.execute(text("INSERT INTO products VALUES (1, 'p1')"))
    return AnonymizingSQLDatabase(eng, include_tables=["products"], anonymizer=None)


def test_passes_static_validate_fails_explain_unknown_column(tmp_path):
    db = _sqlite_db(tmp_path)
    query = "SELECT nonexistent_column FROM products"
    allowed = set(db.get_usable_table_names())
    static = _validate_sql_query(query, allowed=allowed)
    assert static == query

    result = explain_validate(db, query)
    assert isinstance(result, ToolRejection)
    assert result.reason.startswith(f"{DRY_RUN_PREFIX}:")
    assert "unknown_column" in result.reason


def test_passes_static_and_explain_control(tmp_path):
    db = _sqlite_db(tmp_path)
    query = "SELECT COUNT(*) FROM products"
    allowed = set(db.get_usable_table_names())
    assert _validate_sql_query(query, allowed=allowed) == query
    assert explain_validate(db, query) is None


def test_run_explain_sqlite_invokes_explain_query_plan(tmp_path):
    db = _sqlite_db(tmp_path)
    seen: list[str] = []
    original_connect = db._engine.connect

    class ConnectionWrapper:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, stmt, params=None):
            seen.append(str(stmt))
            return self._inner.execute(stmt, params)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._inner.close()
            return False

    def spy_connect():
        return ConnectionWrapper(original_connect())

    db._engine.connect = spy_connect  # type: ignore[method-assign]

    run_explain(db, "SELECT COUNT(*) FROM products")
    assert len(seen) == 1
    assert seen[0].startswith("EXPLAIN QUERY PLAN SELECT COUNT(*)")


def test_explain_validate_uses_injected_explain_fn(tmp_path):
    db = _sqlite_db(tmp_path)
    calls: list[str] = []

    def spy_explain(_db, query: str) -> None:
        calls.append(query)

    assert explain_validate(db, "SELECT COUNT(*) FROM products", explain_fn=spy_explain) is None
    assert calls == ["SELECT COUNT(*) FROM products"]


def test_no_allowlist_wrap_only_calls_explain_fn_not_validate_twice(tmp_path):
    """C-no-allowlist-wrap: dry_run must invoke connector EXPLAIN, not re-validate."""
    db = _sqlite_db(tmp_path)
    query = "SELECT nonexistent_column FROM products"
    validate_calls = 0
    explain_calls = 0

    original_validate = _validate_sql_query

    def counting_validate(q, *, allowed):
        nonlocal validate_calls
        validate_calls += 1
        return original_validate(q, allowed=allowed)

    def spy_explain(_db, _query: str) -> None:
        nonlocal explain_calls
        explain_calls += 1
        raise RuntimeError("no such column: nonexistent_column")

    import app.eval.sql.agent.graph as sql_graph

    sql_graph._validate_sql_query = counting_validate  # type: ignore[assignment]
    try:
        allowed = set(db.get_usable_table_names())
        assert original_validate(query, allowed=allowed) == query
        result = explain_validate(db, query, explain_fn=spy_explain)
    finally:
        sql_graph._validate_sql_query = original_validate  # type: ignore[assignment]

    assert explain_calls == 1
    assert validate_calls == 0
    assert isinstance(result, ToolRejection)
    assert "unknown_column" in result.reason


def test_sanitized_reason_strips_secret_like_codes():
    raw = f"{DRY_RUN_PREFIX}: password=secret mysql://user:pass@host/db"
    sanitized = _assert_sanitized_reason(raw)
    assert "password" not in sanitized.lower()
    assert "://" not in sanitized
    assert sanitized == f"{DRY_RUN_PREFIX}: explain_error"


def test_stable_reason_from_exception_unknown_column():
    exc = Exception('no such column: "bad_col"')
    reason = _stable_reason_from_exception(exc)
    assert reason == f"{DRY_RUN_PREFIX}: unknown_column"
    assert "bad_col" not in reason


@pytest.mark.parametrize(
    ("msg", "code"),
    [
        ("Unknown column 'x' in 'field list'", "unknown_column"),
        ("no such table: missing", "unknown_table"),
        ('near "SELECT": syntax error', "syntax_error"),
        ("something else entirely", "explain_error"),
    ],
)
def test_stable_reason_mapping(msg, code):
    assert _stable_reason_from_exception(Exception(msg)) == f"{DRY_RUN_PREFIX}: {code}"
