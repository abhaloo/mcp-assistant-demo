"""Case- and whitespace-insensitive matching of planner values against allowed_values."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.business_query.definitions.schema import DimensionDefinition

FilterValue = str | int | float | bool


def normalise(value: object) -> str:
    """Collapse whitespace and fold case so `cancelled` and `CANCELLED ` compare equal."""
    return " ".join(str(value).split()).casefold()


def canonical_allowed_values(
    definition: DimensionDefinition, values: Sequence[FilterValue]
) -> list[FilterValue] | None:
    """Stored spellings for values that name a listed value; None when any value is outside."""
    if not definition.allowed_values:
        return list(values)
    by_norm: dict[str, str] = {}
    for stored in definition.allowed_values:
        key = normalise(stored)
        prior = by_norm.get(key)
        if prior is not None and prior != stored:
            return None
        by_norm[key] = stored
    out: list[FilterValue] = []
    for value in values:
        stored = by_norm.get(normalise(value))
        if stored is None:
            return None
        out.append(stored)
    return out
