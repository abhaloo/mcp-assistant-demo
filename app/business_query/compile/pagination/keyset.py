"""Keyset pagination plan transformations and position extraction."""

from __future__ import annotations

from typing import Any

from app.business_query.compile.derived_measures import has_share_measure
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Answered, PlanRefused
from app.business_query.plan import (
    BusinessQueryPlan,
    FilterGroup,
    OrderClause,
    PlanFilter,
)


def plan_is_pageable(plan: BusinessQueryPlan, bundle: DefinitionBundle) -> bool:
    """A comparison or share measure never pages: a second page would join a different
    previous slice or recompute the window over a partial denominator."""
    return plan.compare_to is None and not has_share_measure(plan, bundle)


def keyset_position_for_answered(answered: Answered) -> dict[str, Any]:
    """Extract the last-row keyset position from an answered page."""
    if answered.plan is None or not answered.rows:
        return {}
    members = [clause.member for clause in answered.plan.order]
    members.extend(dim for dim in answered.plan.dimensions if dim not in members)
    last_row = answered.rows[-1]
    return {member: last_row[member] for member in members if member in last_row}


def _find_resource_primary_key(resource_name: str, bundle: DefinitionBundle) -> str | None:
    for dim in bundle.dimensions:
        if dim.owning_resource == resource_name and dim.is_primary_key:
            return dim.name
    return None


def apply_keyset_to_plan(
    plan: BusinessQueryPlan,
    bundle: DefinitionBundle,
    keyset_position: dict[str, Any],
    page_size: int,
) -> BusinessQueryPlan:
    """Transform a BusinessQueryPlan into a deterministic keyset-paginated query plan."""
    if not plan_is_pageable(plan, bundle):
        raise PlanRefused("grain_unexpressible", check_site="unpageable_plan")
    order_items: list[tuple[str, str]] = []
    updated_dimensions = list(plan.dimensions)

    if plan.grain == "entity_rows":
        if plan.order:
            order_items = [(clause.member, clause.direction) for clause in plan.order]
        owning_resources = {
            dim.owning_resource for dim in bundle.dimensions if dim.name in plan.dimensions
        }
        for resource in sorted(owning_resources):
            pk_dim = _find_resource_primary_key(resource, bundle)
            if pk_dim:
                if pk_dim not in updated_dimensions:
                    updated_dimensions.append(pk_dim)
                if pk_dim not in [m for m, _ in order_items]:
                    order_items.append((pk_dim, "asc"))
        if not order_items and updated_dimensions:
            order_items = [(updated_dimensions[0], "asc")]
    elif plan.grain == "grouped":
        if plan.order:
            order_items = [(clause.member, clause.direction) for clause in plan.order]
        else:
            order_items = [(dim, "asc") for dim in plan.dimensions]
            if plan.bucket_set and plan.bucket_set not in plan.dimensions:
                order_items.append((plan.bucket_set, "asc"))
    else:
        return plan.model_copy(update={"limit": page_size})

    updated_order = [OrderClause(member=m, direction=d) for m, d in order_items]

    if not keyset_position:
        return plan.model_copy(
            update={
                "dimensions": updated_dimensions,
                "order": updated_order,
                "limit": page_size,
            }
        )

    active_keyset = [
        (member, direction, keyset_position[member])
        for member, direction in order_items
        if member in keyset_position and keyset_position[member] is not None
    ]
    if not active_keyset:
        return plan.model_copy(
            update={
                "dimensions": updated_dimensions,
                "order": updated_order,
                "limit": page_size,
            }
        )

    if len(active_keyset) == 1:
        member, direction, value = active_keyset[0]
        op = "gt" if direction == "asc" else "lt"
        keyset_filter: PlanFilter | FilterGroup = PlanFilter(
            member=member, operator=op, values=[value]
        )
    else:
        branches: list[PlanFilter | FilterGroup] = []
        for i in range(len(active_keyset)):
            member_i, direction_i, value_i = active_keyset[i]
            op_i = "gt" if direction_i == "asc" else "lt"
            curr_cond = PlanFilter(member=member_i, operator=op_i, values=[value_i])
            if i == 0:
                branches.append(curr_cond)
            else:
                eq_conditions: list[PlanFilter | FilterGroup] = [
                    PlanFilter(
                        member=active_keyset[j][0],
                        operator="eq",
                        values=[active_keyset[j][2]],
                    )
                    for j in range(i)
                ]
                eq_conditions.append(curr_cond)
                branches.append(FilterGroup(all=eq_conditions))
        keyset_filter = FilterGroup(any=branches)

    if plan.filters is not None:
        combined_filters = FilterGroup(all=[plan.filters, keyset_filter])
    else:
        combined_filters = FilterGroup(all=[keyset_filter])

    return plan.model_copy(
        update={
            "dimensions": updated_dimensions,
            "filters": combined_filters,
            "order": updated_order,
            "limit": page_size,
        }
    )
