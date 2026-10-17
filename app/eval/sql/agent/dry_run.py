"""Pre-execute EXPLAIN dry_run for sql_db_query.

Runs on pre-transform SQL after static ``_validate_sql_query`` passes.
Failures surface as ``ToolRejection`` with sanitized ``dry_run_failed: …`` reasons.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from app.tools.types import ToolRejection

DRY_RUN_PREFIX = "dry_run_failed"

ExplainFn = Callable[[Any, str], None]

# Test seam: patch ``explain_fn_override`` or pass ``explain_fn=`` to ``explain_validate``.
explain_fn_override: ExplainFn | None = None

_FORBIDDEN_REASON_PATTERNS = (
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"://"),
    re.compile(r"@\w+\.\w+"),
    re.compile(r"mysql://", re.IGNORECASE),
    re.compile(r"sqlite://", re.IGNORECASE),
    re.compile(r"mariadb://", re.IGNORECASE),
    re.compile(r"\bdsn\b", re.IGNORECASE),
)


def _stable_reason_from_exception(exc: Exception) -> str:
    """Map driver errors to stable reason codes — never echo raw exception text."""
    msg = str(exc).lower()
    if "no such column" in msg or "unknown column" in msg:
        code = "unknown_column"
    elif "no such table" in msg or "unknown table" in msg:
        code = "unknown_table"
    elif "syntax error" in msg or 'near "' in msg or "near '" in msg:
        code = "syntax_error"
    else:
        code = "explain_error"
    return f"{DRY_RUN_PREFIX}: {code}"


def _assert_sanitized_reason(reason: str) -> str:
    for pattern in _FORBIDDEN_REASON_PATTERNS:
        if pattern.search(reason):
            return f"{DRY_RUN_PREFIX}: explain_error"
    return reason


def run_explain(db: Any, query: str) -> None:
    """Execute dialect-appropriate EXPLAIN; raise on connector failure."""
    dialect = str(getattr(db, "dialect", "") or "").lower()
    if dialect == "sqlite":
        explain_sql = f"EXPLAIN QUERY PLAN {query}"
    else:
        explain_sql = f"EXPLAIN {query}"
    with db._engine.connect() as connection:
        connection.execute(text(explain_sql))


def explain_validate(
    db: Any,
    query: str,
    *,
    explain_fn: ExplainFn | None = None,
) -> ToolRejection | None:
    """Return ``ToolRejection`` when EXPLAIN fails; ``None`` when dry_run passes."""
    fn = explain_fn if explain_fn is not None else explain_fn_override
    if fn is None:
        fn = run_explain
    try:
        fn(db, query)
    except Exception as exc:  # noqa: BLE001 — mapped to stable reason
        reason = _assert_sanitized_reason(_stable_reason_from_exception(exc))
        return ToolRejection(reason)
    return None
