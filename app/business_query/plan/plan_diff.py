"""Query plan diff classification and plan digest."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.business_query.plan.query_plan import (
    BusinessPeriod,
    BusinessQueryPlan,
    plan_fingerprint,
)

DiffCategory = Literal[
    "projection",
    "limit",
    "order",
    "period",
    "filter",
    "membership",
    "grain",
    "anchor",
]

_DIFF_CATEGORY_ORDER: tuple[DiffCategory, ...] = (
    "projection",
    "limit",
    "order",
    "period",
    "filter",
    "membership",
    "grain",
    "anchor",
)


class PlanDigest(BaseModel):
    """Compact structured summary of a query plan."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    grain: Literal["scalar", "grouped", "entity_rows"] | str
    anchor: str | None = None
    dimensions: tuple[str, ...] = ()
    measures: tuple[str, ...] = ()
    limit: int | None = None
    period: BusinessPeriod | None = None
    set_ids: tuple[str, ...] = ()
    plan_fingerprint: str

    @field_validator("dimensions", "measures", "set_ids", mode="before")
    @classmethod
    def _coerce_tuples(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


def plan_digest(plan: BusinessQueryPlan) -> PlanDigest:
    """Compute a PlanDigest from a BusinessQueryPlan."""
    return PlanDigest(
        grain=plan.grain,
        anchor=plan.anchor,
        dimensions=tuple(plan.dimensions),
        measures=tuple(plan.measures),
        limit=plan.limit,
        period=plan.period,
        set_ids=tuple(derived.id for derived in plan.derived_sets),
        plan_fingerprint=plan_fingerprint(plan),
    )


def classify_plan_diff(
    before: BusinessQueryPlan,
    after: BusinessQueryPlan,
) -> tuple[DiffCategory, ...]:
    """Classify semantic differences between two query plans.

    Returns an empty tuple when both plans have matching fingerprints.
    Otherwise, returns a tuple of DiffCategory values in fixed canonical order:
    projection, limit, order, period, filter, membership, grain, anchor.
    """
    if plan_fingerprint(before) == plan_fingerprint(after):
        return ()

    detected: list[DiffCategory] = []

    if (
        before.dimensions != after.dimensions
        or before.measures != after.measures
        or before.detail_selections != after.detail_selections
    ):
        detected.append("projection")

    if before.limit != after.limit:
        detected.append("limit")

    if before.order != after.order:
        detected.append("order")

    if before.period != after.period or before.compare_to != after.compare_to:
        detected.append("period")

    if (
        before.filters != after.filters
        or before.having != after.having
        or before.attribute_predicates != after.attribute_predicates
    ):
        detected.append("filter")

    if before.derived_sets != after.derived_sets or before.bucket_set != after.bucket_set:
        detected.append("membership")

    if before.grain != after.grain:
        detected.append("grain")

    if before.anchor != after.anchor:
        detected.append("anchor")

    return tuple(detected)
