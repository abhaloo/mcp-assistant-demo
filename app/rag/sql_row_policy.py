"""SQL row RLS types and parameterized query transform (Finding S1).

Vanna-shaped control flow: transform(query) → ScopedSqlQuery | SqlToolRejection.
Predicates use named bind params via sqlglot AST — never string-injected IDs.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.guardrails.sql_guard import is_safe_select, referenced_tables
from app.rag.sql_table_scope import TableScopeDecl, UnknownTableScope, get_table_scope

__all__ = [
    "SqlToolRejection",
    "ScopedSqlQuery",
    "SqlQueryArgTransform",
    "get_table_scope",
    "UnknownTableScope",
    "TableScopeDecl",
]

_GENERIC_DENY = "structured query is not permitted"


@dataclass(frozen=True, slots=True)
class SqlToolRejection:
    reason: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class ScopedSqlQuery:
    sql: str
    params: dict[str, object]


class SqlQueryArgTransform:
    """Apply declaration-driven entity scope to a SELECT via parameterized AST."""

    def transform(
        self,
        query: str,
        *,
        entity_id: int | None,
        cross_entity: bool,
        department_id: int | None = None,  # noqa: ARG002 — D5(a): entity-only this slice
    ) -> ScopedSqlQuery | SqlToolRejection:
        try:
            root = sqlglot.parse_one(query, read="mysql")
        except sqlglot.errors.ParseError:
            return SqlToolRejection(_GENERIC_DENY, "unparseable")
        if root is None:
            return SqlToolRejection(_GENERIC_DENY, "unparseable")

        # CTEs: fail-closed until every base occurrence is provably scoped.
        if list(root.find_all(exp.CTE)):
            return SqlToolRejection(_GENERIC_DENY, "unprovable_scope")

        tables = referenced_tables(query)
        decls: dict[str, TableScopeDecl] = {}
        for name in tables:
            try:
                decls[name] = get_table_scope(name)
            except UnknownTableScope:
                return SqlToolRejection(_GENERIC_DENY, "unknown_table")

        needs_entity = any(d.kind == "scoped" for d in decls.values())
        if needs_entity and not cross_entity and entity_id is None:
            return SqlToolRejection(_GENERIC_DENY, "missing_entity_scope")

        if cross_entity or not needs_entity:
            ok, _reason = is_safe_select(query)
            if not ok:
                return SqlToolRejection(_GENERIC_DENY, "unsafe_after_transform")
            return ScopedSqlQuery(sql=query, params={})

        params: dict[str, object] = {}
        counters: dict[str, int] = {}
        for table_node in list(root.find_all(exp.Table)):
            name = table_node.name.lower()
            decl = decls.get(name)
            if decl is None or decl.kind != "scoped" or decl.entity is None:
                continue
            n = counters.get(name, 0)
            counters[name] = n + 1
            bind_key = f"scope_entity_{name}_{n}"
            params[bind_key] = entity_id
            entity_col = decl.entity
            inner = (
                exp.select(exp.Star())
                .from_(exp.table_(table_node.this, db=table_node.args.get("db")))
                .where(exp.column(entity_col).eq(exp.Placeholder(this=bind_key)))
            )
            alias = table_node.alias_or_name or table_node.name
            table_node.replace(exp.Subquery(this=inner, alias=alias))

        if needs_entity and not params:
            return SqlToolRejection(_GENERIC_DENY, "missing_bind_site")

        scoped_sql = root.sql(dialect="mysql")
        ok, _reason = is_safe_select(scoped_sql)
        if not ok:
            return SqlToolRejection(_GENERIC_DENY, "unsafe_after_transform")

        return ScopedSqlQuery(sql=scoped_sql, params=params)
