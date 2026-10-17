"""Derived measure expression compiler for ratio and share metrics."""

from __future__ import annotations

from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from app.business_query.definitions import DefinitionBundle, measure_for_member
from app.business_query.definitions.schema import DimensionDefinition, MeasureDefinition
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import BusinessQueryPlan
from app.business_query.ports import CompilerAdapter


def has_share_measure(plan: BusinessQueryPlan, bundle: DefinitionBundle) -> bool:
    """A share measure divides by a window; a page would split its denominator."""
    for member in plan.measures:
        measure = measure_for_member(bundle, member)
        if measure is not None and measure.expression_kind == "share":
            return True
    return False


def _guarded_division(
    numerator: ColumnElement[Any], denominator: ColumnElement[Any]
) -> ColumnElement[Any]:
    """NULL when the denominator is zero, spelled as CASE rather than NULLIF: MariaDB drops a
    HAVING clause that contains NULLIF whenever the select list carries a window function,
    and every grouped statement carries the total-row-count window."""
    return sa.case((denominator == 0, sa.null()), else_=numerator / denominator)


def derived_measure_expression(
    adapter: CompilerAdapter,
    measure: MeasureDefinition,
    tables: dict[str, sa.Table],
    *,
    label: str,
    business_date: date | None,
    measures_by_name: dict[str, MeasureDefinition],
    plan: BusinessQueryPlan,
) -> ColumnElement[Any]:
    """Compile derived measure arithmetic for ratio and share expressions.

    Ratio divides the numerator by the denominator; a zero denominator yields NULL.
    Share divides the base measure by its sum over a window. The window partitions
    by currency when grouped by currency; otherwise it uses an empty window.
    """
    if measure.expression_kind == "ratio":
        expression = _ratio_expression(
            adapter,
            measure,
            tables,
            business_date=business_date,
            measures_by_name=measures_by_name,
        )
    elif measure.expression_kind == "share":
        expression = _share_expression(
            adapter,
            measure,
            tables,
            business_date=business_date,
            measures_by_name=measures_by_name,
            plan=plan,
        )
    else:
        raise PlanRefused("grain_unexpressible")
    return expression.label(label)


def _ratio_expression(
    adapter: CompilerAdapter,
    measure: MeasureDefinition,
    tables: dict[str, sa.Table],
    *,
    business_date: date | None,
    measures_by_name: dict[str, MeasureDefinition],
) -> ColumnElement[Any]:
    """Numerator over denominator, refusing anything but exactly two components."""
    if len(measure.derived_from) != 2:
        raise PlanRefused("grain_unexpressible", check_site="ratio_arity")
    a_name, b_name = measure.derived_from[0], measure.derived_from[1]
    a_measure = measures_by_name.get(a_name)
    b_measure = measures_by_name.get(b_name)
    if a_measure is None or b_measure is None:
        raise PlanRefused("member_not_found")
    a_table = tables.get(a_measure.owning_resource)
    b_table = tables.get(b_measure.owning_resource)
    if a_table is None or b_table is None:
        raise PlanRefused("no_join_path")
    a_expr = adapter._measure_expr(a_measure, a_table, a_name, business_date=business_date)
    b_expr = adapter._measure_expr(b_measure, b_table, b_name, business_date=business_date)
    return _guarded_division(a_expr, b_expr)


def _share_expression(
    adapter: CompilerAdapter,
    measure: MeasureDefinition,
    tables: dict[str, sa.Table],
    *,
    business_date: date | None,
    measures_by_name: dict[str, MeasureDefinition],
    plan: BusinessQueryPlan,
) -> ColumnElement[Any]:
    """The base measure over its own window sum; the window follows the grouped currency."""
    if plan.grain == "scalar":
        raise PlanRefused("grain_unexpressible", check_site="share_requires_grouping")
    if not measure.derived_from:
        raise PlanRefused("grain_unexpressible")
    base_name = measure.derived_from[0]
    base_measure = measures_by_name.get(base_name)
    if base_measure is None:
        raise PlanRefused("member_not_found")
    base_table = tables.get(base_measure.owning_resource)
    if base_table is None:
        raise PlanRefused("no_join_path")
    base_expr = adapter._measure_expr(
        base_measure, base_table, base_name, business_date=business_date
    )

    currency_expr = _currency_partition_expr(
        adapter, base_measure, tables, business_date=business_date, plan=plan
    )
    if currency_expr is not None:
        denominator = sa.func.sum(base_expr).over(partition_by=[currency_expr])
    else:
        denominator = sa.func.sum(base_expr).over()
    return _guarded_division(base_expr, denominator)


def _currency_partition_expr(
    adapter: CompilerAdapter,
    measure: MeasureDefinition,
    tables: dict[str, sa.Table],
    *,
    business_date: date | None,
    plan: BusinessQueryPlan,
) -> ColumnElement[Any] | None:
    """The grouped currency column the share window partitions by, when the plan groups one."""
    if not measure.currency_dimension or not plan.dimensions:
        return None
    for dim_alias in plan.dimensions:
        kind, dim_def = adapter._resolve_capability(dim_alias)
        if (
            kind == "dimension"
            and isinstance(dim_def, DimensionDefinition)
            and (
                dim_def.name == measure.currency_dimension
                or dim_alias == measure.currency_dimension
            )
        ):
            dim_table = tables.get(dim_def.owning_resource)
            if dim_table is None:
                raise PlanRefused("no_join_path")
            return adapter._dimension_expr(
                dim_def, dim_table, dim_alias, business_date=business_date
            )
    return None
