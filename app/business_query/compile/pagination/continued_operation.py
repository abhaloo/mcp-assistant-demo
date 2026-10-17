"""Load a stored plan, apply an optional patch, and re-authorize the result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from app.auth import Principal
from app.business_query.authorize.scoping import (
    ForcedPredicate,
    ScopedDerivedSet,
    ScopeDenied,
    ScopedPlan,
    bind_scope_context,
    canonical_forced,
)
from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.compile.pagination.scope_binding import ScopeBinding
from app.business_query.definitions import DefinitionBundle
from app.business_query.plan.plan_diff import DiffCategory, classify_plan_diff
from app.business_query.plan.plan_patch import PlanPatch, PlanPatchRefused, apply_plan_patch
from app.business_query.plan.query_plan import BusinessQueryPlan

ContinuationReason = Literal[
    "subject_unknown",
    "principal_mismatch",
    "bundle_changed",
    "scope_mismatch",
    "patch_refused",
    "unauthorized_member",
]


@dataclass(frozen=True)
class ContinuationRefused:
    reason: ContinuationReason


def _call_scope_fn(
    scope_fn: Any,
    plan: BusinessQueryPlan,
    principal: Principal,
    bundle: DefinitionBundle,
    business_date: date | None,
) -> ScopedPlan:
    try:
        return scope_fn(plan, principal, bundle, business_date=business_date)
    except TypeError:
        return scope_fn(plan, principal, bundle)


def _binding_refusal(
    stored: StoredPlan, principal: Principal, bundle: DefinitionBundle, project_id: str
) -> ContinuationRefused | None:
    """The stored plan binds a principal, a bundle and a scope; any of the three
    changing refuses the continuation."""
    if stored.principal is not None and str(stored.principal) != str(principal.user_id):
        return ContinuationRefused(reason="principal_mismatch")
    if stored.bundle_hash not in (None, "") and stored.bundle_hash != bundle.content_hash:
        return ContinuationRefused(reason="bundle_changed")
    caller = ScopeBinding.from_principal(
        principal,
        project_id=project_id,
        bundle_hash=bundle.content_hash,
    )
    if not ScopeBinding.from_stored_plan(stored).verify_stored(
        caller,
        caller,
        project_id,
        bundle.content_hash,
    ):
        return ContinuationRefused(reason="scope_mismatch")
    return None


def _reconcile_forced_predicates(
    reauthorized_forced: tuple[ForcedPredicate, ...],
    stored_forced: tuple[ForcedPredicate, ...],
    *,
    is_derived: bool = False,
) -> tuple[ForcedPredicate, ...]:
    """Reconcile reauthorized forced predicates against stored forced predicates."""
    curr_principal = [p for p in reauthorized_forced if p.source == "principal_scope"]
    stored_principal = [p for p in stored_forced if p.source == "principal_scope"]

    if stored_principal or is_derived:
        if canonical_forced(curr_principal) != canonical_forced(stored_principal):
            raise ScopeDenied("principal scope mismatch")

    curr_record = [p for p in reauthorized_forced if p.source == "record_referent"]
    stored_record = [p for p in stored_forced if p.source == "record_referent"]

    if curr_record and canonical_forced(curr_record) != canonical_forced(stored_record):
        raise ScopeDenied("record referent mismatch")

    merged = list(curr_principal)
    seen = {item.model_dump_json() for item in merged}
    for item in stored_record:
        dump = item.model_dump_json()
        if dump not in seen:
            merged.append(item)
            seen.add(dump)
    return tuple(merged)


def restore_stored_scope(
    scoped: ScopedPlan,
    stored: StoredPlan,
    *,
    principal: Principal,
    bundle: DefinitionBundle,
) -> ScopedPlan:
    """Restore stored response policy, frozen business date, and forced predicates."""
    frozen_date = (
        stored.derived_payload.business_date if stored.derived_payload is not None else None
    )
    scoped = bind_scope_context(
        scoped,
        principal=principal,
        bundle_hash=bundle.content_hash,
        business_date=frozen_date,
        response_policy=stored.response_policy,
    )

    is_derived = stored.derived_payload is not None
    reconciled_outer_forced = _reconcile_forced_predicates(
        scoped.forced,
        stored.forced,
        is_derived=is_derived,
    )
    scoped = scoped.model_copy(update={"forced": reconciled_outer_forced})

    snapshots_by_id = (
        {s.id: s.forced for s in stored.derived_payload.derived}
        if stored.derived_payload is not None
        else {}
    )
    if set(snapshots_by_id.keys()) != {d.id for d in scoped.derived}:
        raise ScopeDenied()

    if stored.derived_payload is not None:
        reconciled_derived: list[ScopedDerivedSet] = []
        for d in scoped.derived:
            inner_stored_forced = snapshots_by_id[d.id]
            reconciled_inner_forced = _reconcile_forced_predicates(
                d.scoped.forced,
                inner_stored_forced,
                is_derived=True,
            )
            reconciled_inner_scoped = d.scoped.model_copy(
                update={"forced": reconciled_inner_forced}
            )
            reconciled_derived.append(
                ScopedDerivedSet(
                    id=d.id,
                    key=d.key,
                    mode=d.mode,
                    scoped=reconciled_inner_scoped,
                )
            )
        scoped = scoped.model_copy(update={"derived": tuple(reconciled_derived)})

    return scoped


async def execute_continued_operation(  # noqa: PLR0913
    *,
    subject: str,
    patch: PlanPatch | None,
    principal: Principal,
    bundle: DefinitionBundle,
    plan_store: Any,
    scope_fn: Any,
    now: datetime,
    business_date: date | None,
    project_id: str,
) -> tuple[ScopedPlan, StoredPlan, tuple[DiffCategory, ...]] | ContinuationRefused:
    stored = await plan_store.get_plan(subject, now=now)
    if stored is None:
        return ContinuationRefused(reason="subject_unknown")
    refused = _binding_refusal(stored, principal, bundle, project_id)
    if refused is not None:
        return refused
    plan = stored.plan
    if patch is not None:
        try:
            plan = apply_plan_patch(plan, patch)
        except PlanPatchRefused:
            return ContinuationRefused(reason="patch_refused")
    try:
        scoped = _call_scope_fn(scope_fn, plan, principal, bundle, business_date)
        scoped = restore_stored_scope(scoped, stored, principal=principal, bundle=bundle)
    except ScopeDenied:
        return ContinuationRefused(reason="unauthorized_member")
    return scoped, stored, classify_plan_diff(stored.plan, plan)
