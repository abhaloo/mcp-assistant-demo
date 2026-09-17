"""Derived-set SQL lowering: compiles scoped derived sets into inner relational statements."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement, Select, operators
from sqlalchemy.sql.elements import BinaryExpression, ClauseElement

from app.business_query.authorize.scoping import ScopedDerivedSet, ScopedPlan
from app.business_query.compile.statement_builder import (
    Projection,
    build_relation,
    measure_projection,
)
from app.business_query.definitions import DimensionDefinition, MeasureDefinition
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import BusinessQueryPlan
from app.business_query.ports import CompilerAdapter


def membership_clause(
    column: ColumnElement[Any],
    relation: Select[Any],
    *,
    key: str,
    set_id: str,
    exclude: bool,
) -> ColumnElement[bool]:
    table = relation.subquery(f"bq_set_{set_id}")
    keys = sa.select(table.c[key])
    predicate = column.not_in(keys) if exclude else column.in_(keys)
    return sa.and_(column.is_not(None), predicate)


def _key_carries_not_null_guard(
    whereclause: ClauseElement | None, key_col: ColumnElement[Any]
) -> bool:
    """Structural check (walks the compiled expression tree, never the SQL
    text) for the ADR 0073 non-NULL-key invariant: the KEY COLUMN itself
    must carry its own ``IS NOT NULL`` predicate. ``not_in(subquery)``
    returns zero rows -- not the complement -- when the subquery yields a
    single NULL, so a plan-level ``not_null`` filter on some OTHER member
    does not satisfy this; only a direct ``key_col IS NOT NULL`` clause
    does."""
    if whereclause is None:
        return False
    clauses = getattr(whereclause, "clauses", (whereclause,))
    return any(
        isinstance(clause, BinaryExpression)
        and clause.operator is operators.is_not
        and clause.left.compare(key_col)
        for clause in clauses
    )


def compile_set_relation(
    adapter: CompilerAdapter,
    derived: ScopedDerivedSet,
) -> Select[Any]:
    scoped = derived.scoped
    plan = scoped.plan
    mode = derived.mode
    key = derived.key

    def set_projection_factory(
        adp: CompilerAdapter,
        pl: BusinessQueryPlan,
        sc: ScopedPlan,
        measure_defs: list[tuple[str, MeasureDefinition]],
        tables: dict[str, sa.Table],
    ) -> tuple[Projection, ColumnElement[bool] | None]:
        kind, definition = adp._resolve_capability(key)
        if kind != "dimension":
            raise PlanRefused("member_not_found")
        assert isinstance(definition, DimensionDefinition)
        table = tables[definition.owning_resource]
        key_col = adp._dimension_expr(definition, table, key, business_date=sc.business_date)
        key_labeled = key_col.label(key)

        _select_cols, measure_labels = measure_projection(adp, pl, sc, measure_defs, tables)

        select_cols = [key_labeled]
        group_cols = [key_col] if pl.grain == "grouped" else []
        dim_exprs = {key: key_col}

        projection = Projection(
            select_cols=select_cols,
            group_cols=group_cols,
            measure_labels=measure_labels,
            dim_exprs=dim_exprs,
        )
        null_predicate = key_col.is_not(None)
        return projection, null_predicate

    relational = build_relation(adapter, scoped, projection_factory=set_projection_factory)
    stmt = relational.statement
    projection = relational.projection

    if mode == "complete":
        if plan.grain == "entity_rows":
            stmt = stmt.distinct()
    elif mode == "ranked":
        order_cols: list[ColumnElement[Any]] = []
        for clause in plan.order:
            if clause.member in projection.measure_labels:
                expr = projection.measure_labels[clause.member]
                order_cols.append(expr.desc() if clause.direction == "desc" else expr.asc())
            elif clause.member in projection.dim_exprs:
                expr = projection.dim_exprs[clause.member]
                order_cols.append(expr.desc() if clause.direction == "desc" else expr.asc())
            elif clause.member == key:
                key_expr = projection.dim_exprs.get(key)
                if key_expr is not None:
                    col = key_expr.desc() if clause.direction == "desc" else key_expr.asc()
                    order_cols.append(col)
        key_expr = projection.dim_exprs.get(key)
        key_ordered = any(clause.member == key for clause in plan.order)
        if not key_ordered and key_expr is not None:
            order_cols.append(key_expr.asc())
        stmt = stmt.order_by(*order_cols)
        stmt = stmt.limit(plan.limit)

    if key not in stmt.selected_columns.keys():
        raise AssertionError(f"derived set key '{key}' missing from compiled selected columns")
    if not _key_carries_not_null_guard(stmt.whereclause, projection.dim_exprs[key]):
        raise AssertionError(
            f"derived set key '{key}' compiled without its own IS NOT NULL guard "
            "(ADR 0073 non-NULL-key invariant)"
        )
    return stmt
