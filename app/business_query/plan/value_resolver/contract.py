"""Value resolver contract types, protocols, and identifier helpers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from app.business_query.plan.plan_tree import PlanPath

ResolverDisposition = Literal["exact", "ambiguous", "none"]
ResolvableType = Literal["customer", "product"]

RESOLVER_VERSION = "s1-resolver-v1"
RESOLVER_CANDIDATE_CAP = 10


def resolver_query_id_for(idempotency_key: str) -> str:
    if not idempotency_key:
        raise ValueError("resolver query idempotency key is required")
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    return f"rq_{digest}"


@dataclass(frozen=True)
class ResolverResult:
    disposition: ResolverDisposition
    value_type: ResolvableType
    member: str
    canonical_values: tuple[str, ...] = ()
    match_count: int = 0
    resolver_version: str = RESOLVER_VERSION
    candidates: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition == "exact" and len(self.canonical_values) != 1:
            raise ValueError("exact resolution requires one canonical value")
        if self.disposition != "exact" and self.canonical_values:
            raise ValueError("non-exact resolution must not disclose candidates")
        if self.match_count < 0 or self.match_count > RESOLVER_CANDIDATE_CAP:
            raise ValueError("resolver match_count is out of bounds")
        if self.disposition == "none" and self.match_count != 0:
            raise ValueError("none resolution must not report authorized matches")


@dataclass(frozen=True)
class ResolverLookup:
    value_type: ResolvableType
    member: str
    raw_value: str
    normalized_value: str
    path: PlanPath = ()
