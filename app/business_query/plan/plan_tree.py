"""Canonical tree traversal and node replacement for query plans."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.business_query.plan.query_plan import BusinessQueryPlan

PlanPath = tuple[str, ...]


def iter_plan_nodes(
    plan: BusinessQueryPlan,
) -> Iterator[tuple[PlanPath, BusinessQueryPlan]]:
    """Yield (path, node) for root plan and every inner derived set."""
    yield ((), plan)
    for s in plan.derived_sets:
        yield ((s.id,), s.plan)


def replace_plan_at_path(
    plan: BusinessQueryPlan,
    path: PlanPath,
    replacement: BusinessQueryPlan,
) -> BusinessQueryPlan:
    """Replace an owning plan node at path and revalidate the full tree."""
    from app.business_query.plan.query_plan import BusinessQueryPlan, DerivedSet

    if path == ():
        return replacement
    if len(path) == 1:
        target_id = path[0]
        found = False
        new_derived: list[DerivedSet] = []
        for s in plan.derived_sets:
            if s.id == target_id:
                new_derived.append(
                    DerivedSet(
                        id=s.id,
                        mode=s.mode,
                        key=s.key,
                        plan=replacement,
                    )
                )
                found = True
            else:
                new_derived.append(s)
        if not found:
            raise KeyError(f"Unknown plan path: {path}")
        return BusinessQueryPlan.model_validate(
            {
                **plan.model_dump(mode="python"),
                "derived_sets": new_derived,
            }
        )
    raise KeyError(f"Unknown or deep plan path: {path}")
