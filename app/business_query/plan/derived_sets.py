"""Structural rules for derived sets: modes, the shape catalogue, and violation text.

``_validate_derived_sets`` itself lives on ``query_plan.BusinessQueryPlan``: it
inspects ``BusinessQueryPlan``/``DerivedSet`` shape directly and is called only
from that model's own ``_grain_shape`` validator, so this module never imports
the plan model. See docs/superpowers/import-cycles-baseline.json."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

_SET_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")

SetMode = Literal["complete", "pick", "ranked"]
# ``ranked`` is the older spelling of a pick by measure; nothing new writes it.
ORDERED_MODES: frozenset[str] = frozenset({"pick", "ranked"})
MAX_DERIVED_SETS = 3


@dataclass(frozen=True, slots=True)
class ShapeRule:
    """One supported set shape, in the words the prompt and the error text share."""

    mode: str
    shape: str


SUPPORTED_SHAPES: tuple[ShapeRule, ...] = (
    ShapeRule(
        "complete",
        "{id, mode: complete, key: <entity id dimension>, plan: {grain: entity_rows | grouped, "
        "dimensions: [<key>], filters / period / having as needed, order: []}} "
        "— every matching key; the inner limit never limits membership",
    ),
    ShapeRule(
        "pick",
        "{id, mode: pick, key: <entity id dimension>, plan: {grain: entity_rows, "
        "dimensions: [<key>], order: [{member: <a dimension of the key's resource, such as its "
        "created_at>, direction: desc | asc}], limit: 1..50}} — the first N rows in that order; "
        "the key ascending is appended for you; NULL values sort last. For a measure basis use "
        "grain grouped, measures: [<one measure>], order: [{member: <that measure>, "
        "direction: desc | asc}]",
    ),
    ShapeRule(
        "time group",
        "{id, mode: pick, key: <a time dimension>, plan: {grain: grouped, measures: [<one "
        "measure>], dimensions: [<that time dimension>], period: {time_dimension: <the same>, "
        "granularity: day | week | month | quarter | year, and one range}, order: [{member: "
        "<that measure>, direction: desc | asc}], limit: 1..50}} — the top periods by the "
        "measure; the answer plan filters the SAME time dimension with in_set",
    ),
    ShapeRule(
        "chain",
        "a set's plan may filter with in_set / not_in_set on a set declared BEFORE it in "
        f"derived_sets; the answer plan may reference any set; at most {MAX_DERIVED_SETS} sets; "
        "a set never contains another set",
    ),
)


def validate_rank_limit(value: object) -> int:
    """Validate that an ordered set limit is an explicit integer between 1 and 50."""
    if type(value) is not int or not 1 <= value <= 50:
        raise ValueError("ranked set requires an explicit limit from 1 to 50")
    return value


def shape_rules_text() -> str:
    """The shape catalogue as prompt lines."""
    return "\n".join(f"- {rule.mode}: {rule.shape}" for rule in SUPPORTED_SHAPES)


def format_violations(violations: Sequence[str]) -> str:
    """Every broken rule first, then the shapes that are allowed.

    The raw-payload explicit limit rule on ordered modes is reported alone before
    construction; all other derived-set rules are reported together here because
    the payload cannot be constructed without an explicit limit in range.
    """
    lines = [f"{len(violations)} derived-set rule(s) broken:"]
    lines.extend(f"- {text}" for text in violations)
    lines.append("Supported shapes:")
    lines.append(shape_rules_text())
    return "\n".join(lines)
