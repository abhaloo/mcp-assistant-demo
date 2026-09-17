"""Typed pagination plan payload models and canonical stored hashing."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from app.business_query.authorize.forced_predicate import ForcedPredicate, canonical_forced
from app.business_query.plan.query_plan import BusinessQueryPlan, canonical_plan_payload

# `stored` below is typed `Any`, not `ports.StoredPlanLike`: this module is
# reached by compile/pagination/plan_store.py (an import-cycle-baseline SCC
# member), and ports.py is itself an SCC member (via ScopedPlan/StoredPlan),
# so importing a protocol from ports.py here would close a new cycle back
# through it. Callers always pass a real ``StoredPlan``, which is all
# `.derived_payload`, `.plan`, `.forced`, `.response_policy` below actually
# read.


class DerivedScopeSnapshot(BaseModel):
    """Scope snapshot for one derived set member."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    forced: tuple[ForcedPredicate, ...]


class DerivedPlanPayload(BaseModel):
    """Envelope version 2 payload for plans containing derived sets."""

    model_config = ConfigDict(strict=True, extra="forbid")

    format_version: Literal[2]
    plan: BusinessQueryPlan
    forced: tuple[ForcedPredicate, ...]
    derived: tuple[DerivedScopeSnapshot, ...]
    business_date: date | None
    response_policy: Literal["allow_partial", "strict"]
    original_question: str | None

    @model_validator(mode="after")
    def _validate_contract(self) -> DerivedPlanPayload:
        if not self.plan.derived_sets:
            raise ValueError("DerivedPlanPayload plan must contain derived_sets")
        plan_set_ids = {s.id for s in self.plan.derived_sets}
        snapshot_ids = [d.id for d in self.derived]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("duplicate derived snapshot ids in payload")
        if set(snapshot_ids) != plan_set_ids:
            raise ValueError("derived snapshots must match plan derived_sets one-to-one")
        return self


def stored_plan_fingerprint(stored: Any) -> str:
    """Compute the canonical hash directly from a StoredPlan."""
    if stored.derived_payload is not None:
        derived_payload = stored.derived_payload
        payload: dict[str, Any] = {
            "plan": canonical_plan_payload(derived_payload.plan),
            "forced": canonical_forced(derived_payload.forced),
            "response_policy": derived_payload.response_policy,
            "business_date": (
                derived_payload.business_date.isoformat()
                if derived_payload.business_date is not None
                else None
            ),
            "derived": [
                {"id": item.id, "forced": canonical_forced(item.forced)}
                for item in sorted(derived_payload.derived, key=lambda item: item.id)
            ],
        }
    else:
        payload = {
            "plan": canonical_plan_payload(stored.plan),
            "forced": canonical_forced(stored.forced),
            "response_policy": stored.response_policy,
        }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
