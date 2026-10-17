"""Shared sqlglot-backed SQL guard helpers (runtime + eval cross-check)."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

_READ_ONLY_ROOTS = (exp.Select, exp.Union)
_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Command,
    exp.Merge,
)


def is_safe_select(query: str) -> tuple[bool, str]:
    """Parser-backed guard (sqlglot, dialect=mysql): single read-only SELECT/CTE only.
    Defense-in-depth — NOT the boundary (the read-only DB credential is)."""
    try:
        statements = [s for s in sqlglot.parse(query, read="mysql") if s is not None]
    except sqlglot.errors.ParseError as e:
        return False, f"unparseable SQL: {e}"
    if len(statements) != 1:
        return False, "only a single SQL statement is allowed"
    root = statements[0]
    if not isinstance(root, _READ_ONLY_ROOTS):
        return False, "only SELECT / WITH…SELECT statements are allowed"
    if root.find(*_FORBIDDEN_NODES) is not None:
        return False, "DML/DDL is not allowed"
    return True, ""


def referenced_tables(query: str) -> set[str]:
    """Every table referenced (subqueries + CTEs included), minus CTE alias names."""
    try:
        root = sqlglot.parse_one(query, read="mysql")
    except sqlglot.errors.ParseError:
        return set()
    cte_aliases = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    tables = {t.name.lower() for t in root.find_all(exp.Table)}
    return tables - cte_aliases
