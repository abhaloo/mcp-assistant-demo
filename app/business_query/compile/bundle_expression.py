"""Qualify a bundle-declared SQL fragment against the table it compiles into."""

from __future__ import annotations

import re

import sqlalchemy as sa

_SQL_NON_COLUMN = frozenset(
    {
        "SUM",
        "AVG",
        "MIN",
        "MAX",
        "COUNT",
        "COALESCE",
        "CASE",
        "WHEN",
        "THEN",
        "ELSE",
        "END",
        "AND",
        "OR",
        "NOT",
        "IN",
        "IS",
        "NULL",
        "AS",
    }
)


def qualify_bundle_expression(expression: str, table: sa.Table) -> str:
    """Qualify bare column names against table.name; leave functions/keywords alone."""
    col_names = {c.name for c in table.c}
    tokens = re.split(r"(\b[A-Za-z_][A-Za-z0-9_]*\b)", expression)
    out: list[str] = []
    for tok in tokens:
        if tok.upper() in _SQL_NON_COLUMN or not re.match(r"^[A-Za-z_]", tok):
            out.append(tok)
        elif tok in col_names and "." not in tok:
            out.append(f"{table.name}.{tok}")
        else:
            out.append(tok)
    return "".join(out)
