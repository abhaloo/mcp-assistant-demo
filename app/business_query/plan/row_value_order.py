"""A row list ranked by an amount sorts by each row's own amount.

A SUM over one column of the measure's own resource has a per-row value: that
column on each row. A row list cannot carry the measure, so a sort by the
measure becomes a sort by the column, and the column is shown. A pick that
ranks whole records by such a measure shows the column on those records' rows.
"""

from __future__ import annotations

import re

from app.business_query.definitions import DefinitionBundle
from app.business_query.plan.filter_tree import PlanFilter, iter_filter_leaves
from app.business_query.plan.query_plan import BusinessQueryPlan, DerivedSet, OrderClause

_BARE_SUM = re.compile(r"^SUM\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)$", re.IGNORECASE)


def _owner(bundle: DefinitionBundle, card_name: str) -> str | None:
    """The owning resource of a card member, or None when it is not a measure or dimension."""
    entry = next((e for e in bundle.capabilities if e.name == card_name), None)
    if entry is None:
        return None
    for definition in (*bundle.measures, *bundle.dimensions):
        if definition.name == entry.resolves_to:
            return definition.owning_resource
    return None


def row_value_member(
    bundle: DefinitionBundle, measure_member: str, visible: frozenset[str]
) -> str | None:
    """The visible card dimension that holds one row's share of a SUM measure."""
    entry = next((e for e in bundle.capabilities if e.name == measure_member), None)
    if entry is None or entry.kind != "measure":
        return None
    measure = next((m for m in bundle.measures if m.name == entry.resolves_to), None)
    if measure is None or measure.agg_type != "sum" or measure.filter_sql is not None:
        return None
    match = _BARE_SUM.match(measure.sql_expression.strip())
    if match is None:
        return None
    for dimension in bundle.dimensions:
        if (
            dimension.owning_resource == measure.owning_resource
            and dimension.sql_expression.strip() == match.group(1)
        ):
            for card in bundle.capabilities:
                if (
                    card.kind == "dimension"
                    and card.resolves_to == dimension.name
                    and card.name in visible
                ):
                    return card.name
    return None


def lower_row_list_ranking(
    plan: BusinessQueryPlan, bundle: DefinitionBundle, visible: frozenset[str]
) -> BusinessQueryPlan:
    """Sort a row list by the per-row value of each measure it is ranked by."""
    if plan.grain != "entity_rows" or not plan.order:
        return plan
    row_resources = {_owner(bundle, name) for name in plan.dimensions}
    if plan.anchor is not None:
        row_resources.add(plan.anchor)
    order: list[OrderClause] = []
    dimensions = list(plan.dimensions)
    for clause in plan.order:
        value = row_value_member(bundle, clause.member, visible)
        if value is None or _owner(bundle, clause.member) not in row_resources:
            order.append(clause)
            continue
        order.append(OrderClause(member=value, direction=clause.direction))
        if value not in dimensions:
            dimensions.append(value)
    if order == plan.order:
        return plan
    return plan.model_copy(update={"order": order, "dimensions": dimensions})


def _pick_rank_value(
    derived: DerivedSet, bundle: DefinitionBundle, visible: frozenset[str]
) -> str | None:
    """The per-row value a pick ranks whole records by, or None.

    The pick groups by its key only, the key is its resource's primary key, and
    the ranking measure is a SUM over a column of that same resource.
    """
    inner = derived.plan
    if derived.mode != "pick" or not inner.order or inner.dimensions != [derived.key]:
        return None
    value = row_value_member(bundle, inner.order[0].member, visible)
    resource = _owner(bundle, derived.key)
    binding = next((r for r in bundle.resources if r.name == resource), None)
    if (
        value is None
        or binding is None
        or derived.key != f"{resource}.{binding.primary_key}"
        or _owner(bundle, value) != resource
    ):
        return None
    return value


def show_pick_rank_value(
    plan: BusinessQueryPlan, bundle: DefinitionBundle, visible: frozenset[str]
) -> BusinessQueryPlan:
    """Show the amount a pick ranked its records by on the rows of those records.

    A pick is a filter, so its measure never reaches the listed rows. When a row
    list keeps the records a pick chose (in_set) and lists that resource, each
    row shows its own share of the ranking measure.
    """
    if plan.grain != "entity_rows" or not plan.derived_sets:
        return plan
    row_resources = {_owner(bundle, name) for name in plan.dimensions}
    if plan.anchor is not None:
        row_resources.add(plan.anchor)
    kept_sets = {
        str(leaf.values[0])
        for leaf in iter_filter_leaves(plan.filters)
        if isinstance(leaf, PlanFilter) and leaf.operator == "in_set" and leaf.values
    }
    dimensions = list(plan.dimensions)
    for derived in plan.derived_sets:
        value = _pick_rank_value(derived, bundle, visible)
        if (
            value is not None
            and derived.id in kept_sets
            and _owner(bundle, value) in row_resources
            and value not in dimensions
        ):
            dimensions.append(value)
    if dimensions == plan.dimensions:
        return plan
    return plan.model_copy(update={"dimensions": dimensions})
