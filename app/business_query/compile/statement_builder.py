"""SQL statement construction -- builds a SQLAlchemy Core SELECT from a
scoped plan + bundle. Does not execute the statement and does not authorize."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.sql import ColumnElement, Select, Selectable

from app.business_query.authorize.scoping import ForcedPredicate, ScopedPlan
from app.business_query.compile.business_time import (
    business_datetime_bounds,
    period_bounds,
    time_filter_bounds,
    within_one_bucket,
)
from app.business_query.compile.derived_measures import (
    derived_measure_expression,
    has_share_measure,
)
from app.business_query.compile.dialect_time import TimeGranularity, time_bucket
from app.business_query.compile.join_paths import (
    ResolvedJoin,
    anchor_resource,
    is_multiplied,
)
from app.business_query.compile.record_label_sidecars import build_record_label_sidecars
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
    anchor: str | None = None
    expanded: str | None = None
    anchor_key: str | None = None
    child_key: str | None = None


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


def partition_forced_predicates(
    adapter: CompilerAdapter,
    outer_forced: tuple[ForcedPredicate, ...],
    anchor: str,
    tables: dict[str, sa.Table],
) -> tuple[dict[str, list[ColumnElement[bool]]], tuple[ForcedPredicate, ...]]:
    """Partition outer forced predicates into lookup join predicates (for ON)
    and anchor predicates (for WHERE)."""
    lookup_forced: dict[str, list[ColumnElement[bool]]] = {}
    for forced in outer_forced:
        if forced.resource != anchor:
            lookup_forced.setdefault(forced.resource, []).extend(
                adapter._forced_predicates((forced,), tables)
            )
    anchor_forced = tuple(forced for forced in outer_forced if forced.resource == anchor)
    return lookup_forced, anchor_forced


def _primary_key_for_resource(
    adapter: CompilerAdapter, plan: BusinessQueryPlan, resource: str
) -> str | None:
    for dim_name, dim_def in getattr(adapter, "_dimensions", {}).items():
        if getattr(dim_def, "owning_resource", None) == resource and getattr(
            dim_def, "is_primary_key", False
        ):
            return dim_name
    id_dim = f"{resource}.id"
    if id_dim in plan.dimensions or (
        hasattr(adapter, "_dimensions") and id_dim in adapter._dimensions
    ):
        return id_dim
    return None


def _effective_resources(
    adapter: CompilerAdapter, plan: BusinessQueryPlan, resources: tuple[str, ...]
) -> tuple[str, ...]:
    if len(resources) <= 1:
        return resources
    child_filter_resources = set(resources[1:]) & {
        "invoice_item",
        "quotation_item",
        "payable_quotation_item",
    }
    if not child_filter_resources:
        return resources
    proj_members = plan.measures + plan.dimensions
    proj_resources = {adapter._resolve_capability(m)[1].owning_resource for m in proj_members}
    if not (proj_resources & child_filter_resources):
        return tuple(r for r in resources if r not in child_filter_resources)
    return resources


def _resolve_anchor_and_expansion(
    adapter: CompilerAdapter,
    plan: BusinessQueryPlan,
    effective_resources: tuple[str, ...],
    join_tree: list[ResolvedJoin],
) -> tuple[str, str | None, str | None, str | None]:
    anchor = anchor_resource(effective_resources, join_tree, declared=plan.anchor)
    anchor_key = _primary_key_for_resource(adapter, plan, anchor)
    expanded: str | None = None
    child_key: str | None = None
    if join_tree and is_multiplied(anchor, join_tree):
        leaves = [name for name in effective_resources if not is_multiplied(name, join_tree)]
        if len(leaves) == 1:
            expanded = leaves[0]
            child_key = _primary_key_for_resource(adapter, plan, expanded)
    return anchor, expanded, anchor_key, child_key


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

    effective_resources = _effective_resources(adapter, plan, resources)
    join_tree = adapter._resolve_join_tree(effective_resources)
    anchor, expanded, anchor_key, child_key = _resolve_anchor_and_expansion(
        adapter, plan, effective_resources, join_tree
    )

    measure_defs = resolve_measures(adapter, plan, join_tree)
    tables: dict[str, sa.Table] = {name: adapter._table_for(name) for name in resources}
    isolated_resources = frozenset(set(resources) - set(effective_resources))
    outer_forced = tuple(
        forced for forced in scoped.forced if forced.resource not in isolated_resources
    )
    lookup_forced, anchor_forced = partition_forced_predicates(
        adapter, outer_forced, anchor, tables
    )
    from_clause = assemble_from_clause(adapter, anchor, join_tree, tables, lookup_forced)
    where_preds = adapter._forced_predicates(anchor_forced, tables)
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
        anchor=anchor,
    )
    where_preds.extend(filter_preds)

    stmt = sa.select(*projection.select_cols).select_from(from_clause)
    if where_preds:
        stmt = stmt.where(sa.and_(*where_preds))
    if projection.group_cols and plan.grain != "entity_rows":
        stmt = stmt.group_by(*projection.group_cols)
    if having_filter is not None:
        stmt = stmt.having(having_filter)

    return RelationalSelect(
        statement=stmt,
        projection=projection,
        anchor=anchor,
        expanded=expanded,
        anchor_key=anchor_key,
        child_key=child_key,
    )


def _expansion_order_cols(
    relational: RelationalSelect,
    dim_exprs: dict[str, ColumnElement[Any]],
    exclude: set[str] | None = None,
) -> list[ColumnElement[Any]]:
    excluded = exclude or set()
    return [
        dim_exprs[k].asc()
        for k in (relational.anchor_key, relational.child_key)
        if k and k in dim_exprs and k not in excluded
    ]


def _compile_order_cols(
    plan: BusinessQueryPlan, projection: _Projection
) -> list[ColumnElement[Any]]:
    order_cols: list[ColumnElement[Any]] = []
    for clause in plan.order:
        if clause.member in projection.measure_labels:
            col = sa.literal_column(clause.member)
        elif clause.member in projection.dim_exprs:
            col = projection.dim_exprs[clause.member]
        elif clause.member in projection.order_exprs:
            col = projection.order_exprs[clause.member]
        elif clause.member == plan.bucket_set:
            col = sa.literal_column(clause.member)
        else:
            raise PlanRefused(
                "member_not_found", "order_member_not_selected", members=(clause.member,)
            )
        order_cols.append(col.asc() if clause.direction == "asc" else col.desc())
    return order_cols


def build_select(adapter: CompilerAdapter, scoped: ScopedPlan) -> Selectable:
    relational = build_relation(adapter, scoped, projection_factory=projection_for_grain)
    plan = scoped.plan
    stmt = relational.statement
    projection = relational.projection

    if plan.order:
        order_cols = _compile_order_cols(plan, projection)
        if relational.expanded is not None:
            order_cols.extend(
                _expansion_order_cols(
                    relational, projection.dim_exprs, {clause.member for clause in plan.order}
                )
            )
        stmt = stmt.order_by(*order_cols)
    elif relational.expanded is not None:
        expansion_cols = _expansion_order_cols(relational, projection.dim_exprs)
        if expansion_cols:
            stmt = stmt.order_by(*expansion_cols)
    elif plan.grain == "grouped" and has_share_measure(plan, adapter._bundle):
        # No second page can follow, so the bounded rows are a top-N snapshot: rank by
        # the measure, then by the group keys so equal values keep a stable order. A
        # comparison reaches this shape through build_comparison_select, which ranks its
        # own joined relation, so in practice this is the share case.
        group_keys = [projection.dim_exprs[dim] for dim in plan.dimensions]
        if plan.bucket_set and plan.bucket_set not in plan.dimensions:
            group_keys.append(sa.literal_column(plan.bucket_set))
        stmt = stmt.order_by(sa.literal_column(plan.measures[0]).desc(), *group_keys)

    stmt = apply_bounded_tail(stmt, plan)
    stmt._relation = relational
    return stmt


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
            raise PlanRefused("member_not_found", "not_a_measure", members=(name,))
        assert isinstance(definition, MeasureDefinition)
        measure_defs.append((name, definition))
        if join_tree and is_multiplied(definition.owning_resource, join_tree):
            raise PlanRefused("fanout_unsafe")
    return measure_defs


def assemble_from_clause(
    adapter: CompilerAdapter,
    anchor: str,
    join_tree: list[ResolvedJoin],
    tables: dict[str, sa.Table],
    lookup_forced: dict[str, list[ColumnElement[bool]]],
) -> sa.sql.FromClause:
    """FROM the anchor, then each edge toward the side not yet joined; optional edges
    are LEFT JOINs so an anchor row with no parent stays. A joined resource's scope
    predicates are part of its ON clause for the same reason."""
    from_clause: sa.sql.FromClause = tables[anchor]
    joined = {anchor}
    pending = list(join_tree)
    while pending:
        progressed = False
        for join in list(pending):
            if join.from_resource in joined and join.to_resource not in joined:
                target = join.to_resource
            elif join.to_resource in joined and join.from_resource not in joined:
                target = join.from_resource
            else:
                continue
            on_clause = sa.and_(parse_on_sql(join, tables), *lookup_forced.get(target, []))
            outer = (
                join.optional
                or (join.from_resource == anchor and join.relationship == "one_to_many")
                or (join.to_resource == anchor and join.relationship == "many_to_one")
            )
            from_clause = from_clause.join(tables[target], on_clause, isouter=outer)
            joined.add(target)
            pending.remove(join)
            progressed = True
        if not progressed:
            raise PlanRefused("no_join_path")
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


def time_bucket_of(
    plan: BusinessQueryPlan, *, timezone: str, business_date: date | None
) -> tuple[str, TimeGranularity] | None:
    """The time dimension a grouped plan groups by calendar bucket, and the bucket.

    A granularity on a grouped plan is honoured or refused, never dropped. When
    the plan does not group by its time dimension, a range inside one bucket is
    a plain range (the planner often repeats the range unit as the granularity);
    a range over more buckets cannot be answered as asked.
    """
    period = plan.period
    if plan.grain != "grouped" or period is None or period.granularity is None:
        return None
    if period.time_dimension in plan.dimensions:
        return period.time_dimension, period.granularity
    bounds = period_bounds(period, timezone, business_date=business_date)
    if within_one_bucket(bounds, period.granularity):
        return None
    raise PlanRefused("grain_unexpressible", check_site="granularity_without_time_dimension")


def dimension_projection(
    adapter: CompilerAdapter,
    plan: BusinessQueryPlan,
    scoped: ScopedPlan,
    tables: dict[str, sa.Table],
) -> dict[str, ColumnElement[Any]]:
    bucket = time_bucket_of(
        plan,
        timezone=adapter._bundle.business_timezone,
        business_date=scoped.business_date,
    )
    dim_exprs: dict[str, ColumnElement[Any]] = {}
    for name in plan.dimensions:
        kind, definition = adapter._resolve_capability(name)
        if kind != "dimension":
            raise PlanRefused("member_not_found", "not_a_dimension", members=(name,))
        assert isinstance(definition, DimensionDefinition)
        table = tables[definition.owning_resource]
        if bucket is not None and bucket[0] == name:
            column = table.c[definition.sql_expression.strip()]
            dim_exprs[name] = time_bucket(column, bucket[1], adapter.dialect_name).label(name)
            continue
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
        raise PlanRefused("member_not_found", "not_a_bucket_set", members=(plan.bucket_set,))
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
    label_exprs: dict[str, ColumnElement[Any]] | None = None,
) -> _Projection:
    if not plan.dimensions:
        raise PlanRefused("grain_unexpressible", check_site="entity_rows_without_dimensions")
    labels = label_exprs or {}
    select_cols: list[ColumnElement[Any]] = list(dim_exprs.values())
    if "invoice" in tables and "id" in tables["invoice"].c:
        select_cols.append(tables["invoice"].c["id"].label(RECORD_REF_INVOICE_ID))
        if "invoice" in labels:
            select_cols.append(labels["invoice"])
    for res_name, table in tables.items():
        if res_name != "invoice" and "id" in table.c:
            sidecar_name = f"__bq_record_ref_{res_name}_id"
            if not any(
                getattr(c, "key", None) == sidecar_name or getattr(c, "name", None) == sidecar_name
                for c in select_cols
            ):
                select_cols.append(table.c["id"].label(sidecar_name))
                if res_name in labels:
                    select_cols.append(labels[res_name])
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
        effective = _effective_resources(adapter, plan, scoped.resources)
        label_exprs = build_record_label_sidecars(
            adapter, scoped, tables, effective_resources=effective
        )
        return entity_rows_projection(
            plan, dim_exprs, tables, order_exprs=order_exprs, label_exprs=label_exprs
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
    anchor: str | None = None,
) -> tuple[list[ColumnElement[bool]], ColumnElement[bool] | None]:
    where_preds: list[ColumnElement[bool]] = []

    parents = {
        join.to_resource: join.from_resource for join in adapter._resolve_join_tree(tuple(tables))
    }
    root_resource = next(
        (parents[name] for name in isolated_resources if name in parents),
        anchor if anchor is not None else (scoped.resources[0] if scoped.resources else None),
    )

    if plan.period is not None:
        kind, definition = adapter._resolve_capability(plan.period.time_dimension)
        if kind != "dimension":
            raise PlanRefused("period_dimension_missing")
        assert isinstance(definition, DimensionDefinition)
        table = tables[definition.owning_resource]
        col = table.c[definition.sql_expression.strip()]
        bounds = time_filter_bounds(
            plan.period,
            adapter._bundle.business_timezone,
            business_date=scoped.business_date,
        )
        if bounds is not None:
            lower, upper = business_datetime_bounds(
                definition.sql_expression.strip(), *bounds, adapter._bundle.business_timezone
            )
            where_preds.extend((col >= lower, col < upper))

    derived_sets = {d.id: d for d in scoped.derived} if scoped.derived else None
    where_filter = adapter._compile_filter_group(
        plan.filters,
        allow_measures=False,
        measure_labels=projection.measure_labels,
        dim_exprs=projection.dim_exprs,
        tables=tables,
        business_date=scoped.business_date,
        root_resource=root_resource,
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
        root_resource=root_resource,
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
                raise PlanRefused(
                    "member_not_found", "order_member_not_selected", members=(clause.member,)
                )
            order_cols.append(col.asc() if clause.direction == "asc" else col.desc())
        stmt = stmt.order_by(*order_cols)

    if plan.grain != "scalar":
        # One statement carries the pre-limit result count on every returned row.
        stmt = stmt.add_columns(sa.func.count().over().label("__bq_total_row_count"))
    if plan.grain == "entity_rows" or plan.limit:
        stmt = stmt.limit(plan.limit)

    return stmt
