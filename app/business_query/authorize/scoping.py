"""Mandatory role-scoping pass — the ForcedPredicate channel."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

from app.auth import Principal
from app.business_query.authorize.capability import owning_resource
from app.business_query.authorize.forced_predicate import ForcedPredicate, canonical_forced
from app.business_query.authorize.preconditions import check_member_visibility
from app.business_query.definitions import DefinitionBundle, ResourceBinding
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import (
    BusinessQueryPlan,
    canonical_plan_payload,
    local_plan_member_names,
)
from app.telemetry.spans import scope_stage_span

if TYPE_CHECKING:
    from app.business_query.wire.request import BusinessQueryOwnerHint

_DENY_MESSAGE = "business query tools are currently unavailable"
_MAX_JOIN_HOPS = 2


class ScopeDenied(Exception):
    """Fail-closed authorization / scope denial. Message is always generic."""

    def __init__(self, message: str = _DENY_MESSAGE) -> None:
        super().__init__(message)


class ScopedDerivedSet(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    id: str
    key: str
    mode: Literal["complete", "ranked"]
    scoped: ScopedPlan


class ScopedPlan(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    plan: BusinessQueryPlan
    resources: tuple[str, ...]
    forced: tuple[ForcedPredicate, ...]
    derived: tuple[ScopedDerivedSet, ...] = ()
    # Bound by the public module. Optional only for pre-v3 scripted adapter tests.
    principal: Principal | None = None
    business_date: date | None = None
    bundle_hash: str | None = None
    response_policy: Literal["allow_partial", "strict"] = "allow_partial"
    # Minted by the module before adapter work so every SQL ledger row is
    # born joinable to the eventual public receipt.
    answer_query_id: str | None = None


ScopedDerivedSet.model_rebuild()


def bind_scope_context(
    scoped: ScopedPlan,
    *,
    principal: Principal,
    bundle_hash: str,
    business_date: date | None,
    response_policy: Literal["allow_partial", "strict"],
) -> ScopedPlan:
    """Bind request-scoped context across the scoped plan tree."""
    bound_derived = tuple(
        ScopedDerivedSet(
            id=item.id,
            key=item.key,
            mode=item.mode,
            scoped=bind_scope_context(
                item.scoped,
                principal=principal,
                bundle_hash=bundle_hash,
                business_date=business_date,
                response_policy=response_policy,
            ),
        )
        for item in scoped.derived
    )
    return scoped.model_copy(
        update={
            "principal": principal,
            "business_date": business_date,
            "bundle_hash": bundle_hash,
            "response_policy": response_policy,
            "derived": bound_derived,
        }
    )


def scoped_plan_fingerprint(scoped: ScopedPlan) -> str:
    """Hash a bound ``ScopedPlan`` together with every mandatory server predicate.

    Accepts ONLY a ``ScopedPlan``. A bare ``BusinessQueryPlan`` has no forced
    predicates or response policy of its own to bind, so there is no
    permissive default here (ADR 0022 S1). Use ``raw_plan_fingerprint`` when
    forced predicates and response policy must be supplied explicitly, ahead
    of a ``ScopedPlan`` existing.
    """
    if not isinstance(scoped, ScopedPlan):
        raise TypeError(
            f"scoped_plan_fingerprint requires a ScopedPlan, got {type(scoped).__name__}; "
            "use raw_plan_fingerprint(plan, forced, response_policy) for a bare plan"
        )
    payload: dict[str, Any] = {
        "plan": canonical_plan_payload(scoped.plan),
        "forced": canonical_forced(scoped.forced),
        "response_policy": scoped.response_policy,
    }
    if scoped.derived:
        payload["business_date"] = (
            scoped.business_date.isoformat() if scoped.business_date is not None else None
        )
        payload["derived"] = [
            {"id": item.id, "forced": canonical_forced(item.scoped.forced)}
            for item in sorted(scoped.derived, key=lambda item: item.id)
        ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def raw_plan_fingerprint(
    plan: BusinessQueryPlan,
    forced: tuple[ForcedPredicate, ...] | list[ForcedPredicate],
    response_policy: Literal["allow_partial", "strict"],
) -> str:
    """Hash a bare plan with EXPLICITLY supplied forced predicates and policy.

    No defaults: an unscoped plan must never silently fingerprint with an
    empty forced-predicate set or the more permissive policy (ADR 0022 S1).
    Prefer ``scoped_plan_fingerprint`` once a ``ScopedPlan`` exists.
    """
    payload: dict[str, Any] = {
        "plan": canonical_plan_payload(plan),
        "forced": canonical_forced(forced),
        "response_policy": response_policy,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_owner_hint_scope(
    scoped: ScopedPlan,
    owner_hint: BusinessQueryOwnerHint,
    bundle: DefinitionBundle,
) -> ScopedPlan:
    """Add a detail-page owner predicate to plans that touch that resource.

    ``owner_hint`` is intentionally not folded into ``BusinessQueryPlan``:
    planner output remains backend-neutral and contains no authorization
    predicates.  A hinted resource absent from the bundle or from
    ``scoped.resources`` raises ``PlanRefused(grain_unexpressible)``.
    """

    by_name = _resources_by_name(bundle)
    binding = by_name.get(owner_hint.resource_type)
    if binding is None:
        raise PlanRefused("grain_unexpressible", check_site="owner_hint_unknown_resource")
    if owner_hint.resource_type not in scoped.resources:
        raise PlanRefused("grain_unexpressible", check_site="owner_hint_unexpressible")

    owner_column = binding.primary_key
    if owner_hint.binding_member is not None:
        dimension = next(
            (
                item
                for item in bundle.dimensions
                if item.name == owner_hint.binding_member
                and item.owning_resource == owner_hint.resource_type
            ),
            None,
        )
        if dimension is None:
            raise PlanRefused("grain_unexpressible", check_site="owner_hint_binding_unexpressible")
        owner_column = dimension.sql_expression

    owner_predicate = ForcedPredicate(
        resource=owner_hint.resource_type,
        column=owner_column,
        operator="eq",
        values=[owner_hint.record_id],
        source="record_referent",
    )
    return scoped.model_copy(update={"forced": (*scoped.forced, owner_predicate)})


def _member_owning_resource(bundle: DefinitionBundle, member: str) -> str | None:
    """Plan members are capability-card names: name → resolves_to → definition owner."""
    for entry in bundle.capabilities:
        if entry.name == member:
            return owning_resource(bundle, entry)
    return None


def _resources_by_name(bundle: DefinitionBundle) -> dict[str, ResourceBinding]:
    return {resource.name: resource for resource in bundle.resources}


def _join_adjacency(bundle: DefinitionBundle) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for join in bundle.joins:
        adjacency[join.from_resource].add(join.to_resource)
        adjacency[join.to_resource].add(join.from_resource)
    return adjacency


def _shortest_path(adjacency: dict[str, set[str]], start: str, goal: str) -> list[str] | None:
    """BFS path of length ≤ _MAX_JOIN_HOPS edges, or None."""
    if start == goal:
        return [start]
    parent: dict[str, str | None] = {start: None}
    queue: deque[str] = deque([start])
    depth = {start: 0}
    while queue:
        current = queue.popleft()
        if depth[current] >= _MAX_JOIN_HOPS:
            continue
        for neighbor in sorted(adjacency.get(current, ())):
            if neighbor in parent:
                continue
            parent[neighbor] = current
            depth[neighbor] = depth[current] + 1
            if neighbor == goal:
                path = [goal]
                node: str | None = goal
                while node != start:
                    node = parent[node]
                    assert node is not None
                    path.append(node)
                path.reverse()
                return path
            queue.append(neighbor)
    return None


def _resolve_join_tree(bundle: DefinitionBundle, required: set[str]) -> tuple[str, ...]:
    """BFS over declared joins (≤2 hops) covering every required resource.

    Single-resource plans are a trivial tree. Multi-resource plans must be
    connected by declared joins; otherwise PlanRefused (fail-closed before
    compilation invents a path) — a capability limit, not an authorization
    decision, so it must not read as a Denied.
    """
    if not required:
        # An empty plan (no members at all) expresses nothing to join — a
        # capability limit, not an authorization decision.
        raise PlanRefused("grain_unexpressible", check_site="empty_join_cover")
    if len(required) == 1:
        return (sorted(required)[0],)

    adjacency = _join_adjacency(bundle)
    cover: set[str] = set(required)
    ordered = sorted(required)
    for i, left in enumerate(ordered):
        for right in ordered[i + 1 :]:
            path = _shortest_path(adjacency, left, right)
            if path is None:
                raise PlanRefused("no_join_path")
            cover.update(path)
    return tuple(sorted(cover))


def _forced_for_resource(resource: ResourceBinding, principal: Principal) -> list[ForcedPredicate]:
    """Mirror record_executor._scope_predicates semantics (entity/dept/recordPredicates)."""
    forced: list[ForcedPredicate] = []

    if not principal.cross_entity:
        entity_column = resource.scope_columns.entity
        if entity_column is None:
            raise ScopeDenied()
        if principal.entity_id is None:
            raise ScopeDenied()
        forced.append(
            ForcedPredicate(
                resource=resource.name,
                column=entity_column,
                operator="eq",
                values=[principal.entity_id],
            )
        )

    department_column = resource.scope_columns.department
    mode = resource.department_scope_mode
    if department_column is None:
        # A resource that REQUIRES department scoping but declares no
        # department column cannot enforce it — fail closed instead of
        # silently running unscoped. "filter_when_present" has nothing
        # mandatory to enforce, so it keeps skipping.
        if mode == "required_match":
            raise ScopeDenied()
    else:
        scope_values = principal.scope_values
        department_id = scope_values.department_id if scope_values else None
        if mode == "none":
            department_id = None
        elif department_id is None and mode == "required_match":
            raise ScopeDenied()
        elif department_id is not None and mode in {"filter_when_present", "required_match"}:
            forced.append(
                ForcedPredicate(
                    resource=resource.name,
                    column=department_column,
                    operator="eq",
                    values=[department_id],
                )
            )

    for column, value in resource.record_predicates.items():
        forced.append(
            ForcedPredicate(
                resource=resource.name,
                column=column,
                operator="eq",
                values=[value],
            )
        )
    return forced


def apply_role_scope(
    plan: BusinessQueryPlan,
    principal: Principal,
    bundle: DefinitionBundle,
    *,
    business_date: date | None = None,
) -> ScopedPlan:
    with scope_stage_span() as span:
        try:
            result = _apply_role_scope_impl(
                plan,
                principal,
                bundle,
                business_date=business_date,
            )
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("bq.outcome", "error")
            raise
        span.set_attribute("bq.outcome", "scoped")
        return result


def _apply_role_scope_impl(
    plan: BusinessQueryPlan,
    principal: Principal,
    bundle: DefinitionBundle,
    *,
    business_date: date | None = None,
) -> ScopedPlan:
    """Mandatory pre-compilation pass: visibility + declaration-driven forced preds."""
    visibility_violation = check_member_visibility(plan, principal, bundle)
    if visibility_violation is not None:
        # Hallucinated names are a capability limit (Unsupported downstream);
        # a real-but-ungranted capability stays a generic denial (Denied) —
        # never disclose which capabilities exist beyond the visible card.
        if visibility_violation.kind == "unknown":
            raise PlanRefused("member_not_found")
        raise ScopeDenied()

    members = local_plan_member_names(plan)
    required_resources: set[str] = set()
    from app.business_query.plan.detail_family import resolve_detail_definition

    for member in members:
        owning = _member_owning_resource(bundle, member)
        if owning is None:
            defn = resolve_detail_definition(member, bundle=bundle)
            if defn is not None:
                owning = defn.owner_resource
            else:
                raise ScopeDenied()
        required_resources.add(owning)

    resources = _resolve_join_tree(bundle, required_resources)
    by_name = _resources_by_name(bundle)
    forced: list[ForcedPredicate] = []
    for name in resources:
        binding = by_name.get(name)
        if binding is None:
            raise ScopeDenied()
        forced.extend(_forced_for_resource(binding, principal))

    derived = tuple(
        ScopedDerivedSet(
            id=d.id,
            key=d.key,
            mode=d.mode,
            scoped=_apply_role_scope_impl(
                d.plan,
                principal,
                bundle,
                business_date=business_date,
            ),
        )
        for d in plan.derived_sets
    )

    from app.business_query.authorize.set_keys import assert_set_key_compatible
    from app.business_query.plan import PlanFilter, iter_filter_leaves

    derived_by_id = {ds.id: ds for ds in derived}
    for leaf in iter_filter_leaves(plan.filters):
        if isinstance(leaf, PlanFilter) and leaf.operator in {"in_set", "not_in_set"}:
            set_id = str(leaf.values[0])
            ds = derived_by_id.get(set_id)
            if ds is None:
                raise ScopeDenied()
            assert_set_key_compatible(leaf.member, ds, bundle)

    return ScopedPlan(
        plan=plan,
        resources=resources,
        forced=tuple(forced),
        derived=derived,
        principal=principal,
        business_date=business_date,
        bundle_hash=bundle.content_hash,
    )
