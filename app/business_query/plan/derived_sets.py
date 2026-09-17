"""Structural validation for complete and ranked derived sets.

``validate_derived_sets`` itself lives on ``query_plan.BusinessQueryPlan`` (as
``_validate_derived_sets``), not here: it is called only from that model's
own ``_grain_shape`` validator, and it inspects ``BusinessQueryPlan``/
``DerivedSet`` shape directly, so keeping it there means this module never
needs to import the plan model it would otherwise only use for a type
annotation -- see docs/superpowers/import-cycles-baseline.json."""

from __future__ import annotations

import re

_SET_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")


def validate_rank_limit(value: object) -> int:
    """Validate that a ranked set limit is an explicit integer between 1 and 50."""
    if type(value) is not int or not 1 <= value <= 50:
        raise ValueError("ranked set requires an explicit limit from 1 to 50")
    return value
