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
from app.business_query.plan.derived_sets import ORDERED_MODES
from app.business_query.plan.query_plan import time_group_for
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


def time_group_clause(column: ColumnElement[Any], derived: ScopedDerivedSet) -> ColumnElement[bool]:
    """Rows dated inside the periods the set selected: lower <= column < upper per period."""
    resolved = derived.resolved
    if resolved is None:
        raise AssertionError(f"time-group set '{derived.id}' compiled before it was resolved")
    if not resolved.periods:
        return sa.false()
    return sa.or_(*(sa.and_(column >= p.lower, column < p.upper) for p in resolved.periods))


def compile_set_relation(
    adapter: CompilerAdapter,
    derived: ScopedDerivedSet,
) -> Select[Any]:
    if time_group_for(derived.key, derived.scoped.plan) is not None:
        raise AssertionError(f"time-group set '{derived.id}' compiled before it was resolved")
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

        # An order basis that is not projected still needs an expression: the basis
        # of a pick over rows is a dimension of the key's resource.
        order_exprs: dict[str, ColumnElement[Any]] = {}
        for clause in pl.order:
            if clause.member == key or clause.member in measure_labels:
                continue
            o_kind, o_def = adp._resolve_capability(clause.member)
            if o_kind != "dimension" or not isinstance(o_def, DimensionDefinition):
                raise PlanRefused("member_not_found")
            order_exprs[clause.member] = adp._dimension_expr(
                o_def, tables[o_def.owning_resource], clause.member, business_date=sc.business_date
            )

        projection = Projection(
            select_cols=select_cols,
            group_cols=group_cols,
            measure_labels=measure_labels,
            dim_exprs=dim_exprs,
            order_exprs=order_exprs,
        )
        null_predicate = key_col.is_not(None)
        return projection, null_predicate

    relational = build_relation(adapter, scoped, projection_factory=set_projection_factory)
    stmt = relational.statement
    projection = relational.projection

    if mode == "complete":
        if plan.grain == "entity_rows":
            stmt = stmt.distinct()
    elif mode in ORDERED_MODES:
        stmt = apply_pick_order(stmt, projection, plan, key)

    if key not in stmt.selected_columns.keys():
        raise AssertionError(f"derived set key '{key}' missing from compiled selected columns")
    if not _key_carries_not_null_guard(stmt.whereclause, projection.dim_exprs[key]):
        raise AssertionError(
            f"derived set key '{key}' compiled without its own IS NOT NULL guard "
            "(ADR 0073 non-NULL-key invariant)"
        )
    return stmt


def apply_pick_order(
    stmt: Select[Any], projection: Projection, plan: BusinessQueryPlan, key: str
) -> Select[Any]:
    """Order by the authored basis with NULLs last, then the key, then keep N.

    The key clause is always last. When the plan orders by the key itself, its
    authored direction is kept; otherwise the key ascending is the tiebreak. A
    ranked set's second authored clause is that tiebreak written out."""
    key_expr = projection.dim_exprs[key]
    key_direction = next((clause.direction for clause in plan.order if clause.member == key), "asc")
    order_cols: list[ColumnElement[Any]] = []
    for clause in plan.order:
        if clause.member == key:
            continue
        # SQLAlchemy clause objects refuse ``bool()``, so the lookup chain is explicit.
        expr = projection.measure_labels.get(clause.member)
        if expr is None:
            expr = projection.dim_exprs.get(clause.member)
        if expr is None:
            expr = projection.order_exprs.get(clause.member)
        if expr is None:
            raise PlanRefused("member_not_found")
        order_cols.append(sa.case((expr.is_(None), 1), else_=0).asc())
        order_cols.append(expr.desc() if clause.direction == "desc" else expr.asc())
    order_cols.append(key_expr.desc() if key_direction == "desc" else key_expr.asc())
    return stmt.order_by(*order_cols).limit(plan.limit)
