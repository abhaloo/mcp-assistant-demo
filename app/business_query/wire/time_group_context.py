"""The set-context phrase for a time-group set: which periods the answer keeps."""

from __future__ import annotations

from typing import Any

from app.business_query.plan.query_plan import time_group_for


def time_group_phrase(derived_set: Any) -> str | None:
    """'the month with the highest'; None for an entity set. The caller names the measure."""
    key = time_group_for(derived_set.key, derived_set.plan)
    if key is None:
        return None
    plan = derived_set.plan
    rank = "highest" if plan.order and plan.order[0].direction == "desc" else "lowest"
    periods = key.granularity if plan.limit == 1 else f"{plan.limit} {key.granularity}s"
    return f"the {periods} with the {rank}"
