"""SQL statement construction -- builds a SQLAlchemy Core SELECT from a
scoped plan + bundle. Does not execute the statement and does not authorize."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement, Select, Selectable

from app.business_query.authorize.scoping import ForcedPredicate, ScopedPlan
from app.business_query.compile.business_time import period_bounds
from app.business_query.compile.derived_measures import (
    derived_measure_expression,
    has_share_measure,
)
from app.business_query.compile.join_paths import (
    ResolvedJoin,
    is_multiplied,
    is_optional_join_child,
)
from app.business_query.definitions import (
    BucketSetDefinition,
    DimensionDefinition,
    MeasureDefinition,
)
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import BusinessQueryPlan
from app.business_query.ports import CompilerAdapter
from app.business_query.seal.receipts import (
    RECORD_REF_CUSTOMER_ID as _RECORD_REF_CUSTOMER_ID,
)
from app.business_query.seal.receipts import (
    RECORD_REF_INVOICE_ID,
)
from app.policy.record_query import compile_half_open_business_period


@dataclass(frozen=True)
class Projection:
    """SELECT/GROUP BY columns for one relational statement call."""

    select_cols: list[ColumnElement[Any]]
    group_cols: list[ColumnElement[Any]]
    measure_labels: dict[str, ColumnElement[Any]]
    dim_exprs: dict[str, ColumnElement[Any]]
    order_exprs: dict[str, ColumnElement[Any]] = field(default_factory=dict)


_Projection = Projection


@dataclass(frozen=True)
class RelationalSelect:
    statement: Select[Any]
    projection: Projection


ProjectionFactory = Callable[
    [
        CompilerAdapter,
        BusinessQueryPlan,
        ScopedPlan,
        list[tuple[str, MeasureDefinition]],
        dict[str, sa.Table],
    ],
    tuple[Projection, ColumnElement[bool] | None],
]


def parse_on_sql(join: ResolvedJoin | Any, tables: dict[str, sa.Table]) -> ColumnElement[bool]:
    left_name, right_name = (p.strip() for p in join.on_sql.split("=", 1))
    left_table = tables[join.from_resource]
    right_table = tables[join.to_resource]
    return left_table.c[left_name] == right_table.c[right_name]


def build_relation(
    adapter: CompilerAdapter,
    scoped: ScopedPlan,
    *,
    projection_factory: ProjectionFactory,
) -> RelationalSelect:
    adapter._assert_authorized(scoped)
    plan = scoped.plan
    resources = scoped.resources
    if not resources:
        raise PlanRefused("no_join_path")

    effective_resources = tuple(resources)
    if len(resources) > 1:
        child_filter_resources = set(resources[1:]) & {
            "invoice_item",
            "quotation_item",
            "payable_quotation_item",
        }
        if child_filter_resources:
            proj_members = plan.measures + plan.dimensions
            proj_resources = {
                adapter._resolve_capability(m)[1].owning_resource for m in proj_members
            }
            if not (proj_resources & child_filter_resources):
                effective_resources = tuple(r for r in resources if r not in child_filter_resources)

    join_tree = adapter._resolve_join_tree(effective_resources)
    measure_defs = resolve_measures(adapter, plan, join_tree)
    assert_entity_rows_not_multiplied(adapter, plan, join_tree)

    tables: dict[str, sa.Table] = {name: adapter._table_for(name) for name in resources}
    from_clause = assemble_from_clause(adapter, effective_resources, join_tree, tables)

    isolated_resources = frozenset(set(resources) - set(effective_resources))
    outer_forced = tuple(
        forced for forced in scoped.forced if forced.resource not in isolated_resources
    )
    where_preds = adapter._forced_predicates(outer_forced, tables)
    projection, bucket_applies = projection_factory(adapter, plan, scoped, measure_defs, tables)
    if bucket_applies is not None:
        where_preds.append(bucket_applies)

    filter_preds, having_filter = where_predicates(
        adapter,
        plan,
        scoped,
        projection,
        tables,
        isolated_resources=isolated_resources,
        isolated_forced=scoped.forced,
    )
    where_preds.extend(filter_preds)

    stmt = sa.select(*projection.select_cols).select_from(from_clause)
    if where_preds:
        stmt = stmt.where(sa.and_(*where_preds))
    if projection.group_cols and plan.grain != "entity_rows":
        stmt = stmt.group_by(*projection.group_cols)
    if having_filter is not None:
        stmt = stmt.having(having_filter)

    return RelationalSelect(statement=stmt, projection=projection)


def build_select(adapter: CompilerAdapter, scoped: ScopedPlan) -> Selectable:
    relational = build_relation(adapter, scoped, projection_factory=projection_for_grain)
    plan = scoped.plan
    stmt = relational.statement
    projection = relational.projection

    if plan.order:
        order_cols = []
        for clause in plan.order:
            col: ColumnElement[Any]
            if clause.member in projection.measure_labels:
                col = sa.literal_column(clause.member)
            elif clause.member in projection.dim_exprs:
                col = projection.dim_exprs[clause.member]
            elif clause.member in projection.order_exprs:
                col = projection.order_exprs[clause.member]
            elif clause.member == plan.bucket_set:
                col = sa.literal_column(clause.member)
            else:
                raise PlanRefused("member_not_found")
            order_cols.append(col.asc() if clause.direction == "asc" else col.desc())
        stmt = stmt.order_by(*order_cols)
    elif plan.grain == "grouped" and has_share_measure(plan, adapter._bundle):
        # No second page can follow, so the bounded rows are a top-N snapshot: rank by
        # the measure, then by the group keys so equal values keep a stable order. A
        # comparison reaches this shape through build_comparison_select, which ranks its
        # own joined relation, so in practice this is the share case.
        group_keys = [projection.dim_exprs[dim] for dim in plan.dimensions]
        if plan.bucket_set and plan.bucket_set not in plan.dimensions:
            group_keys.append(sa.literal_column(plan.bucket_set))
        stmt = stmt.order_by(sa.literal_column(plan.measures[0]).desc(), *group_keys)

    return apply_bounded_tail(stmt, plan)


def apply_bounded_tail(stmt: Select[Any], plan: BusinessQueryPlan) -> Select[Any]:
    """Add the pre-limit row count and the row bound shared by every bounded statement."""
    if plan.grain != "scalar":
        stmt = stmt.add_columns(sa.func.count().over().label("__bq_total_row_count"))
    if plan.grain == "entity_rows" or plan.limit:
        stmt = stmt.limit(plan.limit)
    return stmt


def resolve_measures(
    adapter: CompilerAdapter, plan: BusinessQueryPlan, join_tree: list[ResolvedJoin]
) -> list[tuple[str, MeasureDefinition]]:
    measure_defs: list[tuple[str, MeasureDefinition]] = []
    for name in plan.measures:
        kind, definition = adapter._resolve_capability(name)
        if kind != "measure":
            raise PlanRefused("member_not_found")
        assert isinstance(definition, MeasureDefinition)
        measure_defs.append((name, definition))
        if join_tree and is_multiplied(definition.owning_resource, join_tree):
            raise PlanRefused("fanout_unsafe")
        if join_tree and is_optional_join_child(definition.owning_resource, join_tree):
            raise PlanRefused("optional_join_unsupported")
    return measure_defs


def assert_entity_rows_not_multiplied(
    adapter: CompilerAdapter, plan: BusinessQueryPlan, join_tree: list[ResolvedJoin]
) -> None:
    if plan.grain != "entity_rows" or not join_tree:
        return
    for name in plan.dimensions:
        kind, definition = adapter._resolve_capability(name)
        if kind != "dimension":
            raise PlanRefused("member_not_found")
        assert isinstance(definition, DimensionDefinition)
        if is_multiplied(definition.owning_resource, join_tree):
            raise PlanRefused("fanout_unsafe")


def assemble_from_clause(
    adapter: CompilerAdapter,
    resources: tuple[str, ...],
    join_tree: list[ResolvedJoin],
    tables: dict[str, sa.Table],
) -> sa.sql.FromClause:
    from_clause: sa.sql.FromClause = tables[resources[0]]
    for join in join_tree:
        on_clause = parse_on_sql(join, tables)
        target_table = tables[join.to_resource]
        from_clause = from_clause.join(target_table, on_clause, isouter=join.optional)
    return from_clause


def measure_projection(
    adapter: CompilerAdapter,
    plan: BusinessQueryPlan,
    scoped: ScopedPlan,
    measure_defs: list[tuple[str, MeasureDefinition]],
    tables: dict[str, sa.Table],
) -> tuple[list[ColumnElement[Any]], dict[str, ColumnElement[Any]]]:
    select_cols: list[ColumnElement[Any]] = []
    measure_labels: dict[str, ColumnElement[Any]] = {}
    measures_by_name = {m.name: m for m in adapter._bundle.measures}
    for name, measure in measure_defs:
        if measure.expression_kind:
            expr = derived_measure_expression(
                adapter,
                measure,
                tables,
                label=name,
                business_date=scoped.business_date,
                measures_by_name=measures_by_name,
                plan=plan,
            )
        else:
            table = tables[measure.owning_resource]
            expr = adapter._measure_expr(measure, table, name, business_date=scoped.business_date)
        select_cols.append(expr)
        measure_labels[name] = expr
    return select_cols, measure_labels


def dimension_projection(
    adapter: CompilerAdapter,
    plan: BusinessQueryPlan,
    scoped: ScopedPlan,
    tables: dict[str, sa.Table],
) -> dict[str, ColumnElement[Any]]:
    dim_exprs: dict[str, ColumnElement[Any]] = {}
    for name in plan.dimensions:
        kind, definition = adapter._resolve_capability(name)
        if kind != "dimension":
            raise PlanRefused("member_not_found")
        assert isinstance(definition, DimensionDefinition)
        table = tables[definition.owning_resource]
        expr = adapter._dimension_expr(definition, table, name, business_date=scoped.business_date)
        dim_exprs[name] = expr
    return dim_exprs


def customer_id_sidecar(
    adapter: CompilerAdapter,
    dim_exprs: dict[str, ColumnElement[Any]],
    tables: dict[str, sa.Table],
) -> ColumnElement[Any]:
    customer_name_dim = adapter._dimensions["invoice.customer_name"]
    customer_table = tables[customer_name_dim.owning_resource]
    return customer_table.c["customer_id"].label(_RECORD_REF_CUSTOMER_ID)


def bucket_set_projection(
    adapter: CompilerAdapter,
    plan: BusinessQueryPlan,
    scoped: ScopedPlan,
    tables: dict[str, sa.Table],
) -> tuple[ColumnElement[Any] | None, ColumnElement[bool] | None]:
    if plan.bucket_set is None:
        return None, None
    kind, definition = adapter._resolve_capability(plan.bucket_set)
    if kind != "bucket_set":
        raise PlanRefused("member_not_found")
    assert isinstance(definition, BucketSetDefinition)
    table = tables[definition.owning_resource]
    case_expr = adapter._bucket_case(
        definition, table, label=plan.bucket_set, business_date=scoped.business_date
    )
    applies = adapter._filter_sql_predicate(definition.applies_filter_sql, table)
    return case_expr, applies


def entity_rows_projection(
    plan: BusinessQueryPlan,
    dim_exprs: dict[str, ColumnElement[Any]],
    tables: dict[str, sa.Table],
    order_exprs: dict[str, ColumnElement[Any]] | None = None,
) -> _Projection:
    if not plan.dimensions:
        raise PlanRefused("grain_unexpressible", check_site="entity_rows_without_dimensions")
    select_cols: list[ColumnElement[Any]] = list(dim_exprs.values())
    if "invoice" in tables:
        select_cols.append(tables["invoice"].c["id"].label(RECORD_REF_INVOICE_ID))
    return _Projection(
        select_cols=select_cols,
        group_cols=[],
        measure_labels={},
        dim_exprs=dim_exprs,
        order_exprs=order_exprs or {},
    )


def projection_for_grain(
    adapter: CompilerAdapter,
    plan: BusinessQueryPlan,
    scoped: ScopedPlan,
    measure_defs: list[tuple[str, MeasureDefinition]],
    tables: dict[str, sa.Table],
) -> tuple[_Projection, ColumnElement[bool] | None]:
    select_cols, measure_labels = measure_projection(adapter, plan, scoped, measure_defs, tables)
    dim_exprs = dimension_projection(adapter, plan, scoped, tables)
    select_cols = select_cols + list(dim_exprs.values())
    group_cols = list(dim_exprs.values())

    if plan.grain == "grouped" and "invoice.customer_name" in dim_exprs:
        customer_id_col = customer_id_sidecar(adapter, dim_exprs, tables)
        select_cols.append(customer_id_col)
        group_cols.append(customer_id_col)

    case_expr, bucket_applies = bucket_set_projection(adapter, plan, scoped, tables)
    if case_expr is not None and plan.grain != "entity_rows":
        select_cols.append(case_expr)
        group_cols.append(case_expr)

    order_exprs: dict[str, ColumnElement[Any]] = {}
    if plan.order:
        for clause in plan.order:
            if (
                clause.member not in dim_exprs
                and clause.member not in measure_labels
                and clause.member != plan.bucket_set
            ):
                try:
                    kind, definition = adapter._resolve_capability(clause.member)
                    if kind == "dimension":
                        assert isinstance(definition, DimensionDefinition)
                        table = tables.get(definition.owning_resource)
                        if table is not None:
                            order_exprs[clause.member] = adapter._dimension_expr(
                                definition, table, clause.member, business_date=scoped.business_date
                            )
                except PlanRefused:
                    pass

    if plan.grain == "entity_rows":
        return entity_rows_projection(
            plan, dim_exprs, tables, order_exprs=order_exprs
        ), bucket_applies

    projection = _Projection(
        select_cols=select_cols,
        group_cols=group_cols,
        measure_labels=measure_labels,
        dim_exprs=dim_exprs,
        order_exprs=order_exprs,
    )
    return projection, bucket_applies


def where_predicates(
    adapter: CompilerAdapter,
    plan: BusinessQueryPlan,
    scoped: ScopedPlan,
    projection: _Projection,
    tables: dict[str, sa.Table],
    *,
    isolated_resources: frozenset[str] = frozenset(),
    isolated_forced: tuple[ForcedPredicate, ...] = (),
) -> tuple[list[ColumnElement[bool]], ColumnElement[bool] | None]:
    where_preds: list[ColumnElement[bool]] = []

    if plan.period is not None:
        kind, definition = adapter._resolve_capability(plan.period.time_dimension)
        if kind != "dimension":
            raise PlanRefused("period_dimension_missing")
        assert isinstance(definition, DimensionDefinition)
        table = tables[definition.owning_resource]
        col = table.c[definition.sql_expression.strip()]
        start, end = period_bounds(
            plan.period,
            adapter._bundle.business_timezone,
            business_date=scoped.business_date,
        )
        clauses = compile_half_open_business_period(
            definition.sql_expression.strip(),
            start.isoformat(),
            end.isoformat(),
            adapter._bundle.business_timezone,
        )
        for clause in clauses:
            value = datetime.fromisoformat(str(clause.value))
            if clause.operator == "gte":
                where_preds.append(col >= value)
            elif clause.operator == "lt":
                where_preds.append(col < value)

    derived_sets = {d.id: d for d in scoped.derived} if scoped.derived else None
    where_filter = adapter._compile_filter_group(
        plan.filters,
        allow_measures=False,
        measure_labels=projection.measure_labels,
        dim_exprs=projection.dim_exprs,
        tables=tables,
        business_date=scoped.business_date,
        root_resource=scoped.resources[0] if scoped.resources else None,
        isolated_resources=isolated_resources,
        isolated_forced=isolated_forced,
        derived_sets=derived_sets,
    )
    if where_filter is not None:
        where_preds.append(where_filter)

    for pred in plan.attribute_predicates:
        from app.business_query.compile.detail_predicates import lower_attribute_predicate
        from app.business_query.plan.detail_family import resolve_detail_definition

        defn = resolve_detail_definition(
            pred.family_key, pred.revision_hash, bundle=adapter._bundle
        )
        if defn is None:
            raise PlanRefused("member_not_found")
        table = tables.get(defn.owner_resource)
        if table is None:
            raise PlanRefused("no_join_path")
        where_preds.append(
            lower_attribute_predicate(
                pred,
                table,
                principal=scoped.principal,
                metadata=adapter._metadata,
                bundle=adapter._bundle,
            )
        )

    having_filter = adapter._compile_filter_group(
        plan.having,
        allow_measures=True,
        measure_labels=projection.measure_labels,
        dim_exprs=projection.dim_exprs,
        tables=tables,
        business_date=scoped.business_date,
        root_resource=scoped.resources[0] if scoped.resources else None,
        isolated_resources=isolated_resources,
        isolated_forced=isolated_forced,
        derived_sets=derived_sets,
    )
    return where_preds, having_filter


def assemble_statement(
    plan: BusinessQueryPlan,
    from_clause: Any,
    projection: _Projection,
    where_preds: list[ColumnElement[bool]],
    having_filter: ColumnElement[bool] | None,
) -> Selectable:
    stmt = sa.select(*projection.select_cols).select_from(from_clause)

    if where_preds:
        stmt = stmt.where(sa.and_(*where_preds))
    if projection.group_cols and plan.grain != "entity_rows":
        stmt = stmt.group_by(*projection.group_cols)
    if having_filter is not None:
        stmt = stmt.having(having_filter)

    if plan.order:
        order_cols = []
        for clause in plan.order:
            col: ColumnElement[Any]
            if clause.member in projection.measure_labels:
                col = sa.literal_column(clause.member)
            elif clause.member in projection.dim_exprs:
                col = projection.dim_exprs[clause.member]
            elif clause.member in projection.order_exprs:
                col = projection.order_exprs[clause.member]
            elif clause.member == plan.bucket_set:
                col = sa.literal_column(clause.member)
            else:
                raise PlanRefused("member_not_found")
            order_cols.append(col.asc() if clause.direction == "asc" else col.desc())
        stmt = stmt.order_by(*order_cols)

    if plan.grain != "scalar":
        # One statement carries the pre-limit result count on every returned row.
        stmt = stmt.add_columns(sa.func.count().over().label("__bq_total_row_count"))
    if plan.grain == "entity_rows" or plan.limit:
        stmt = stmt.limit(plan.limit)

    return stmt
