"""Period comparison compilation behind StatementCompiler."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.sql import Join, Selectable, Subquery

from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.compile.business_time import comparison_bounds
from app.business_query.compile.statement_builder import (
    RelationalSelect,
    apply_bounded_tail,
    build_relation,
    build_select,
    projection_for_grain,
)
from app.business_query.outcomes import PlanRefused, comparison_member
from app.business_query.plan import BusinessPeriod, BusinessQueryPlan

if TYPE_CHECKING:
    from app.business_query.ports import CompilerAdapter

__all__ = ["build_comparison_select", "select_for_plan"]


def select_for_plan(adapter: CompilerAdapter, scoped: ScopedPlan) -> Selectable:
    """Select the appropriate statement compiler for the plan."""
    if scoped.plan.compare_to is None:
        return build_select(adapter, scoped)
    return build_comparison_select(adapter, scoped)


def build_comparison_select(adapter: CompilerAdapter, scoped: ScopedPlan) -> Selectable:
    """Compile two time-bounded relations joined on their group identity."""
    plan = scoped.plan
    if plan.grain == "entity_rows":
        raise PlanRefused("grain_unexpressible", check_site="comparison_grain")

    current, previous = _comparison_relations(adapter, scoped)
    keys = [column.name for column in current.projection.group_cols]
    cur = current.statement.subquery("bq_cmp_current")
    prev = previous.statement.subquery("bq_cmp_previous")
    joined = _join_on_group_keys(cur, prev, keys)

    stmt = sa.select(*_comparison_columns(plan, cur, prev, keys)).select_from(joined)

    valid_order_members = set(plan.measures) | set(plan.dimensions)
    if plan.bucket_set is not None:
        valid_order_members.add(plan.bucket_set)

    if plan.order:
        order_cols: list[sa.sql.ColumnElement[object]] = []
        for clause in plan.order:
            if clause.member not in valid_order_members or clause.member not in cur.c:
                raise PlanRefused("member_not_found")
            col = cur.c[clause.member]
            order_cols.append(col.asc() if clause.direction == "asc" else col.desc())
        stmt = stmt.order_by(*order_cols)
    elif plan.grain != "scalar":
        # A bounded comparison is a top-N snapshot: rank by the current value, then
        # by the group keys so equal values keep a stable order.
        stmt = stmt.order_by(cur.c[plan.measures[0]].desc(), *[cur.c[key] for key in keys])

    return apply_bounded_tail(stmt, plan)


def _comparison_relations(
    adapter: CompilerAdapter, scoped: ScopedPlan
) -> tuple[RelationalSelect, RelationalSelect]:
    """Build the current and previous relations for one comparison."""
    plan = scoped.plan
    assert plan.period is not None
    assert plan.compare_to is not None

    (_, _), (prev_start, prev_end) = comparison_bounds(
        plan.period,
        plan.compare_to,
        adapter._bundle.business_timezone,
        business_date=scoped.business_date,
    )

    current = build_relation(
        adapter,
        scoped.model_copy(update={"plan": plan.model_copy(update={"compare_to": None})}),
        projection_factory=projection_for_grain,
    )
    prev_period = BusinessPeriod(
        time_dimension=plan.period.time_dimension,
        between=(prev_start, prev_end - timedelta(days=1)),
    )
    prev_plan = plan.model_copy(update={"compare_to": None, "period": prev_period, "having": None})
    previous = build_relation(
        adapter,
        scoped.model_copy(update={"plan": prev_plan}),
        projection_factory=projection_for_grain,
    )
    return current, previous


def _join_on_group_keys(cur: Subquery, prev: Subquery, keys: list[str]) -> Join:
    """Join both relations on their group identity; a scalar comparison joins on true."""
    if keys:
        return cur.outerjoin(
            prev, sa.and_(*[cur.c[key].is_not_distinct_from(prev.c[key]) for key in keys])
        )
    return cur.join(prev, sa.true())


def _comparison_columns(
    plan: BusinessQueryPlan, cur: Subquery, prev: Subquery, keys: list[str]
) -> list[sa.sql.ColumnElement[object]]:
    """The group keys plus the current, previous, delta, and delta-percent columns."""
    select_cols: list[sa.sql.ColumnElement[object]] = [cur.c[key] for key in keys]
    for member in plan.measures:
        delta = cur.c[member] - prev.c[member]
        select_cols.append(cur.c[member])
        select_cols.append(prev.c[member].label(comparison_member(member, "previous")))
        select_cols.append(delta.label(comparison_member(member, "delta")))
        select_cols.append(
            (delta / sa.func.nullif(sa.func.abs(prev.c[member]), 0)).label(
                comparison_member(member, "delta_pct")
            )
        )
    return select_cols
