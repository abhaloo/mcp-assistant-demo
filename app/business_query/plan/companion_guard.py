"""A companion plan is planned on its own and never sees another plan's answer.

A list that runs beside a ranking, over the same records with no restriction of
its own, lists every ranked record: it cannot be the records of the group the
ranking picks. Such a set answers a dependent part with the wrong rows.
"""

from __future__ import annotations

import json

from app.business_query.plan.filter_tree import iter_filter_leaves
from app.business_query.plan.planned_set import PlannedQuerySet
from app.business_query.plan.query_plan import BusinessPeriod, BusinessQueryPlan


def _ranks_groups(plan: BusinessQueryPlan) -> bool:
    """A grouped plan ordered by one of its own measures picks groups."""
    return (
        plan.grain == "grouped"
        and bool(plan.dimensions)
        and bool(plan.order)
        and plan.order[0].member in plan.measures
    )


def _resource(member: str) -> str:
    return member.split(".", 1)[0]


def _range(period: BusinessPeriod | None) -> BusinessPeriod | None:
    return None if period is None else period.model_copy(update={"granularity": None})


def _filter_keys(plan: BusinessQueryPlan) -> set[str]:
    return {
        json.dumps(leaf.model_dump(mode="json"), sort_keys=True)
        for leaf in iter_filter_leaves(plan.filters)
    }


def lists_the_ranked_records(ranked: BusinessQueryPlan, listed: BusinessQueryPlan) -> bool:
    """True when ``listed`` lists the records ``ranked`` ranks, restricted no further."""
    return (
        _ranks_groups(ranked)
        and listed.grain == "entity_rows"
        and not listed.derived_sets
        and {_resource(d) for d in listed.dimensions} == {_resource(ranked.dimensions[0])}
        and _filter_keys(listed) <= _filter_keys(ranked)
        and _range(listed.period) == _range(ranked.period)
    )


def dependent_companion(planned: PlannedQuerySet) -> bool:
    """True when one plan of the set lists the records another plan ranks."""
    plans = (planned.primary, *planned.companions)
    return any(
        lists_the_ranked_records(ranked, listed)
        for ranked in plans
        for listed in plans
        if listed is not ranked
    )
