"""Bind set filters to the declared anchor's key (spec §4.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.business_query.plan.filter_tree import PlanFilter, map_filter_tree

if TYPE_CHECKING:
    from app.business_query.definitions import DefinitionBundle
    from app.business_query.plan.filter_tree import AttributePredicate
    from app.business_query.plan.query_plan import BusinessQueryPlan


def bind_set_filters_to_anchor(
    plan: BusinessQueryPlan, bundle: DefinitionBundle
) -> tuple[BusinessQueryPlan, tuple[str, ...]]:
    """Bind in_set/not_in_set filters targeting an anchor pick set to the anchor key."""
    if plan.anchor is None or not plan.derived_sets or plan.filters is None:
        return plan, ()

    anchor_key = f"{plan.anchor}.id"
    pick_sets = {d.id: d.key for d in plan.derived_sets if d.mode == "pick" and d.key == anchor_key}
    if not pick_sets:
        return plan, ()

    rebound: list[str] = []

    def _rebind(leaf: PlanFilter | AttributePredicate) -> PlanFilter | AttributePredicate:
        if (
            isinstance(leaf, PlanFilter)
            and leaf.operator in ("in_set", "not_in_set")
            and leaf.values
            and str(leaf.values[0]) in pick_sets
            and leaf.member != anchor_key
        ):
            rebound.append(leaf.member)
            return leaf.model_copy(update={"member": anchor_key})
        return leaf

    new_filters = map_filter_tree(plan.filters, _rebind)
    if not rebound:
        return plan, ()
    return plan.model_copy(update={"filters": new_filters}), tuple(rebound)
