"""Shared precondition checks over a plan + bundle (+ principal).

Each rule has exactly ONE implementation here instead of one per caller.
``module.py`` maps a returned violation to its outcome (ClarificationRequired
/ Unsupported / Denied / Incomplete), keeping today's exact user-facing
wording. ``internal_adapter.py`` maps the SAME violation to PlanRefused /
ScopeDenied, as it did before this module existed. Only the number of
implementations changes — not the observable behavior of any single caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from app.auth import Principal
from app.business_query.authorize.capability import detail_permissions_satisfied, visible_members
from app.business_query.definitions import DefinitionBundle
from app.business_query.plan import (
    AttributePredicate,
    BusinessQueryPlan,
    guaranteed_single_equality,
    iter_filter_leaves,
    plan_member_names,
)


@dataclass(frozen=True)
class MemberVisibilityViolation:
    """A plan member is either unknown to the bundle, or a known capability
    that is not currently visible to this principal (missing permission,
    ``capability_state`` disabled, or ``decision_pending``)."""

    kind: Literal["unknown", "unauthorized"]
    unknown_members: tuple[str, ...] = ()


def check_member_visibility(
    plan: BusinessQueryPlan, principal: Principal, bundle: DefinitionBundle
) -> MemberVisibilityViolation | None:
    """Every member the plan touches must resolve to a visible capability.

    'unknown' is a capability limit (nothing in the bundle answers to that
    name — the single most common LLM failure). 'unauthorized' is a real
    capability this principal cannot see; the caller must fail closed
    generically and never disclose which capability it was.
    """
    allowed = set(visible_members(principal, bundle))

    members = plan_member_names(plan)
    if members.issubset(allowed):
        # A family may expose several immutable revisions with different
        # Billing grants. Family-level visibility is not enough when a plan
        # explicitly pins one revision; inspect every attribute node before
        # allowing the scoped compiler to proceed.
        for predicate in _attribute_predicates(plan):
            if predicate.revision_hash is None:
                continue
            revisions = [
                detail
                for detail in bundle.detail_definitions
                if detail.family_key == predicate.family_key
                and detail.revision_hash == predicate.revision_hash
            ]
            if not revisions or not detail_permissions_satisfied(revisions[0], principal):
                return MemberVisibilityViolation(kind="unauthorized")
        for selection in plan.detail_selections:
            if selection.revision_mode == "as_of":
                return MemberVisibilityViolation(kind="unauthorized")
            if selection.revision_mode == "exact" and selection.revision_hash is None:
                return MemberVisibilityViolation(kind="unauthorized")
            if selection.revision_mode != "exact" and selection.revision_hash is not None:
                return MemberVisibilityViolation(kind="unauthorized")
            revisions = [
                detail
                for detail in bundle.detail_definitions
                if detail.family_key == selection.family
            ]
            if selection.revision_hash is not None:
                revisions = [
                    detail
                    for detail in revisions
                    if detail.revision_hash == selection.revision_hash
                ]
            if not revisions:
                return MemberVisibilityViolation(kind="unauthorized")
            if selection.revision_hash is None and selection.revision_mode == "current":
                revisions = [detail for detail in revisions if detail.is_current]
            if selection.revision_mode in {"current", "exact"} and len(revisions) != 1:
                return MemberVisibilityViolation(kind="unauthorized")
            if not revisions or any(
                not detail_permissions_satisfied(detail, principal) for detail in revisions
            ):
                return MemberVisibilityViolation(kind="unauthorized")
        return None
    known = {entry.name for entry in bundle.capabilities} | {
        detail.family_key for detail in bundle.detail_definitions
    }
    if not members.issubset(known):
        return MemberVisibilityViolation(
            kind="unknown", unknown_members=tuple(sorted(members - known))
        )
    return MemberVisibilityViolation(kind="unauthorized")


def _attribute_predicates(plan: BusinessQueryPlan) -> list[AttributePredicate]:
    """Collect top-level and nested typed predicates for revision auth."""
    predicates = list(plan.attribute_predicates)
    for group in (plan.filters, plan.having):
        for node in iter_filter_leaves(group):
            if isinstance(node, AttributePredicate):
                predicates.append(node)
    return predicates


@dataclass(frozen=True)
class NativeCurrencyViolation:
    """A currency-formatted measure fails the native-currency rule."""

    kind: Literal["not_declared", "alias_not_visible", "not_constrained"]
    measure_member: str
    currency_members: frozenset[str] = field(default_factory=frozenset)


def check_native_currency_for_measure(
    *,
    measure_member: str,
    currency_dimension: str | None,
    plan: BusinessQueryPlan,
    allowed: frozenset[str] | set[str],
    bundle: DefinitionBundle,
) -> NativeCurrencyViolation | None:
    """Core native-currency rule for ONE measure already known to need it.

    Callers decide WHICH measures require the check — that gating is
    caller-specific in the existing, tested behavior (module checks any
    measure with a declared ``currency_dimension``, and so does the adapter)
    and stays with each caller so neither
    side's tested behavior changes.
    """
    if currency_dimension is None:
        return NativeCurrencyViolation(kind="not_declared", measure_member=measure_member)
    currency_members = frozenset(
        capability.name
        for capability in bundle.capabilities
        if capability.kind == "dimension" and capability.resolves_to == currency_dimension
    )
    visible_aliases = currency_members & allowed
    if not visible_aliases:
        return NativeCurrencyViolation(
            kind="alias_not_visible",
            measure_member=measure_member,
            currency_members=currency_members,
        )
    grouped = plan.grain == "grouped" and bool(visible_aliases & set(plan.dimensions))
    filtered = any(
        guaranteed_single_equality(plan.filters, alias) is not None for alias in visible_aliases
    )
    if grouped or filtered:
        return None
    return NativeCurrencyViolation(
        kind="not_constrained", measure_member=measure_member, currency_members=visible_aliases
    )


@dataclass(frozen=True)
class BusinessDateViolation:
    """The plan's period or bucket_set is anchored to 'today' but no
    business_date was supplied."""


def check_business_date_requirement(
    plan: BusinessQueryPlan, bundle: DefinitionBundle, business_date: date | None
) -> BusinessDateViolation | None:
    if business_date is not None:
        return None
    if plan.period is not None and (
        plan.period.relative is not None or plan.period.since is not None
    ):
        return BusinessDateViolation()
    if plan.bucket_set is None:
        return None
    # Resolve bucket_set through the capability (name -> resolves_to) —
    # never index bucket_sets by the plan's alias directly. The definition
    # carrying relative_to_business_date is keyed by its OWN name, which can
    # differ from the alias a plan names.
    for entry in bundle.capabilities:
        if entry.name == plan.bucket_set and entry.kind == "bucket_set":
            for bucket in bundle.bucket_sets:
                if bucket.name == entry.resolves_to and bucket.relative_to_business_date:
                    return BusinessDateViolation()
            break
    return None
