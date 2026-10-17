"""Resolve projection-view columns to base-table columns with sqlglot lineage."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage

from app.coverage.model import Binding, ColumnBinding, ComputedBinding, UnresolvedBinding
from app.coverage.schema_inventory import SchemaInventory, SchemaView

DIALECT = "mysql"
ViewLineage = dict[tuple[str, str], list[Binding]]


_PARSE_CACHE: dict[str, exp.Expression | None] = {}


def _parse(view_sql: str) -> exp.Expression | None:
    """Parse a view SQL statement into a syntax tree."""
    if view_sql in _PARSE_CACHE:
        return _PARSE_CACHE[view_sql]
    try:
        parsed = sqlglot.parse_one(view_sql, read=DIALECT)
    except SqlglotError:
        parsed = None
    _PARSE_CACHE[view_sql] = parsed
    return parsed


def view_columns(view_sql: str) -> list[str]:
    """Extract output column names in SELECT order."""
    try:
        tree = _parse(view_sql)
        return [] if tree is None else [s.alias_or_name for s in tree.selects]
    except SqlglotError:
        return []


def resolve_view_column_all(view_sql: str, column: str) -> list[Binding]:
    """Resolve all base-table or computed bindings for a view column."""
    try:
        tree = _parse(view_sql)
        if tree is None:
            return [UnresolvedBinding(reason="parse_error")]
        if column not in {s.alias_or_name for s in tree.selects}:
            return [UnresolvedBinding(reason="column_not_in_view")]
        try:
            node = lineage(column, view_sql, dialect=DIALECT)
        except SqlglotError as err:
            return [UnresolvedBinding(reason=f"lineage_error:{type(err).__name__}")]
        found: list[Binding] = []
        for n in node.walk():
            if n is node or not isinstance(n.source, exp.Table):
                continue
            table_name = n.source.name.strip('"`')
            column_name = n.name.split(".")[-1].strip('"`')
            binding = ColumnBinding(table=table_name, column=column_name)
            if binding not in found:
                found.append(binding)
        if found:
            return found
        select_expr = next(s for s in tree.selects if s.alias_or_name == column)
        return [ComputedBinding(expression=select_expr.sql(dialect=DIALECT))]
    except SqlglotError:
        return [UnresolvedBinding(reason="parse_error")]


def resolve_projection_column(
    source: str,
    column: str,
    lineage: ViewLineage,
    inventory: SchemaInventory,
) -> list[Binding]:
    """Bind one column of a projection source through view lineage or the base schema.

    A source that the schema holds as a view resolves through its lineage. The
    sentinel entry (source, "*") marks a view whose definition did not parse, so
    every one of its columns reports "parse_error". A source that the schema holds
    as a base table binds directly. Any other source is not in the schema at all.
    """
    if (source, column) in lineage:
        return list(lineage[(source, column)])
    if (source, "*") in lineage:
        return [UnresolvedBinding(reason="parse_error")]
    if source in inventory.views:
        return [UnresolvedBinding(reason=f"view_column_missing:{source}.{column}")]
    if source in inventory.tables:
        return [ColumnBinding(table=source, column=column)]
    return [UnresolvedBinding(reason=f"projection_not_in_schema:{source}")]


def lineage_for_views(views: dict[str, SchemaView]) -> ViewLineage:
    """Map each view and column pair to its resolved bindings."""
    out: ViewLineage = {}
    for name, view in views.items():
        columns = view_columns(view.definition)
        for column in columns:
            out[(name, column)] = resolve_view_column_all(view.definition, column)
        if not columns:
            out[(name, "*")] = [UnresolvedBinding(reason="parse_error")]
    return out
