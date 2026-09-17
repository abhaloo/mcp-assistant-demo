"""Ordered set of plans from one planner call. Wrap/unwrap is a later task."""

from __future__ import annotations

from dataclasses import dataclass

from app.business_query.plan.query_plan import BusinessQueryPlan


@dataclass(frozen=True)
class PlannedQuerySet:
    primary: BusinessQueryPlan
    companions: tuple[BusinessQueryPlan, ...] = ()
