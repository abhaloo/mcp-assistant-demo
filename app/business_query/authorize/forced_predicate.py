"""``ForcedPredicate`` and its canonical serialization.

Standalone leaf module: no dependency on scoping.py or any other
authorize/compile/plan module, so a consumer needing only the forced-
predicate shape (e.g. compile/pagination/plan_payload.py) can import it
without pulling in scoping.py's much larger surface -- see
docs/superpowers/import-cycles-baseline.json. ``authorize.scoping``
re-exports both names for every existing consumer.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ForcedPredicate(BaseModel):
    """A scope predicate the SERVER forces.

    Bound to a resource + physical column, never to a capability-card member —
    members are what the planner may name; forced predicates are what the
    server imposes.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    resource: str
    column: str
    operator: Literal["eq", "in"]
    values: list[str | int | float | bool]
    source: Literal["principal_scope", "record_referent"] = "principal_scope"


def canonical_forced(
    predicates: tuple[ForcedPredicate, ...] | list[ForcedPredicate],
) -> list[dict[str, Any]]:
    """Serialize and sort forced predicates canonically."""
    return sorted(
        (predicate.model_dump(mode="json") for predicate in predicates),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
