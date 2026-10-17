"""Canonical plan-shape key for query plans.

Two plans share a key if and only if they emit identical SQL structure across
both internal and Cube adapters. Parameter values and limits are excluded.
"""

from __future__ import annotations

from app.business_query.plan.filter_tree import (
    AttributePredicate,
    PlanFilter,
    iter_filter_leaves,
)
from app.business_query.plan.query_plan import (
    BusinessPeriod,
    BusinessQueryPlan,
)


def _leaf_key(leaf: PlanFilter | AttributePredicate) -> str:
    if isinstance(leaf, PlanFilter):
        return f"{leaf.member}:{leaf.operator}"
    return f"{leaf.family_key}:{leaf.operator}"


def _period_key(period: BusinessPeriod | None) -> str:
    if period is None:
        return ""
    range_kind = ""
    if period.relative is not None:
        range_kind = "relative"
    elif period.on is not None:
        range_kind = "on"
    elif period.since is not None:
        range_kind = "since"
    elif period.between is not None:
        range_kind = "between"
    granularity = period.granularity or ""
    return f"{period.time_dimension}:{granularity}:{range_kind}"


def _compare_to_key(compare_to: object) -> str:
    if compare_to is None:
        return ""
    if isinstance(compare_to, str):
        return compare_to
    return "period"


def plan_shape_key(plan: BusinessQueryPlan) -> str:
    """Canonical string of everything that changes emitted SQL, excluding values and limits."""
    filter_leaves = sorted(_leaf_key(leaf) for leaf in iter_filter_leaves(plan.filters))
    having_leaves = sorted(_leaf_key(leaf) for leaf in iter_filter_leaves(plan.having))
    ap_families = sorted(pred.family_key for pred in plan.attribute_predicates)
    ds_families = sorted(sel.family for sel in plan.detail_selections)
    order_clauses = [f"{clause.member}:{clause.direction}" for clause in plan.order]
    derived_sets = [f"{s.mode}:{s.key}:{{{plan_shape_key(s.plan)}}}" for s in plan.derived_sets]

    parts = [
        str(plan.grain),
        f"a={plan.anchor or ''}",
        f"m={','.join(sorted(plan.measures))}",
        f"d={','.join(sorted(plan.dimensions))}",
        f"b={plan.bucket_set or ''}",
        f"f={','.join(filter_leaves)}",
        f"h={','.join(having_leaves)}",
        f"ap={','.join(ap_families)}",
        f"ds={','.join(ds_families)}",
        f"o={','.join(order_clauses)}",
        f"p={_period_key(plan.period)}",
        f"c={_compare_to_key(plan.compare_to)}",
        f"s={','.join(derived_sets)}",
    ]
    return " | ".join(parts)
