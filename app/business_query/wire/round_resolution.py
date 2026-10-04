"""Round resolution: patch continuation or fresh planning for one turn."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal

from app.business_query.authorize.capability import (
    available_access_labels,
    outside_access_labels,
    visible_members,
)
from app.business_query.authorize.scoping import ScopeDenied, ScopedPlan
from app.business_query.compile.pagination.continued_operation import restore_stored_scope
from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import (
    ClarificationRequired,
    Denied,
    Incomplete,
    RefusalDetail,
    Unsupported,
)
from app.business_query.plan.anchor_filters import bind_set_filters_to_anchor
from app.business_query.plan.companion_guard import dependent_companion
from app.business_query.plan.display_members import with_display_members
from app.business_query.plan.plan_diff import DiffCategory
from app.business_query.plan.planned_set import PlannedQuerySet
from app.business_query.plan.row_value_order import (
    lower_row_list_ranking,
    show_pick_rank_value,
)
from app.business_query.ports import BusinessProgressSink
from app.business_query.seal.events import answer_query_id_for
from app.business_query.seal.evidence import BusinessQueryEvidenceContext
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace
from app.core.turn_budget import TurnBudget


@dataclass(frozen=True)
class PlanContinuation:
    subject: str
    stored: StoredPlan
    changes: tuple[DiffCategory, ...]
    tier: Literal["patch"] = "patch"


@dataclass(frozen=True)
class RoundResolution:
    request: BusinessQueryRequest
    planned_set: PlannedQuerySet
    bundle: DefinitionBundle
    continuation: PlanContinuation | None = None
    # Member names pushed into the primary plan's dimensions by the display
    # projection; consumed by the seal to mark those columns role="display".
    display_members_added: frozenset[str] = frozenset()


Resolution = RoundResolution | ClarificationRequired | Denied | Incomplete | Unsupported


def normalize_round(resolution: RoundResolution) -> RoundResolution:
    """Normalize the primary plan before authorization.

    Binds set filters under a declared anchor to the anchor's key, sorts a
    ranked row list by its per-row value, shows the amount a pick ranked its
    records by, then projects each projected key's declared look before the
    round is authorized.
    """
    primary, _ = bind_set_filters_to_anchor(resolution.planned_set.primary, resolution.bundle)
    visible = visible_members(resolution.request.principal, resolution.bundle)
    primary = lower_row_list_ranking(primary, resolution.bundle, visible)
    primary = show_pick_rank_value(primary, resolution.bundle, visible)
    primary, added = with_display_members(primary, resolution.bundle, visible)
    if primary == resolution.planned_set.primary and not added:
        return resolution
    return RoundResolution(
        request=resolution.request,
        planned_set=replace(resolution.planned_set, primary=primary),
        bundle=resolution.bundle,
        continuation=resolution.continuation,
        display_members_added=added,
    )


attach_display_members = normalize_round


async def resolve_round(  # noqa: PLR0913
    module: Any,
    request: BusinessQueryRequest,
    *,
    progress: BusinessProgressSink | None = None,
    evidence: BusinessQueryEvidenceContext | None = None,
    trace: QueryTrace,
    turn_budget: TurnBudget,
) -> Resolution:
    """Resolve a round via continuation or fresh planning."""
    if request.patch is not None:
        from app.business_query.wire.continued_query import continue_with_patch

        patched = await continue_with_patch(module, request, evidence=evidence)
        if isinstance(patched, RoundResolution):
            return normalize_round(patched)
        if isinstance(patched, (ClarificationRequired, Denied, Incomplete, Unsupported)):
            return patched
        request = request.model_copy(update={"patch": None})

    plan_res = await module.plan_round(
        request, progress=progress, trace=trace, turn_budget=turn_budget
    )
    if isinstance(plan_res, Unsupported) and plan_res.detail is None:
        return _explained_planner_refusal(module, request, plan_res, trace)
    if not isinstance(plan_res, tuple):
        return plan_res
    request, planned_set, bundle = plan_res
    if dependent_companion(planned_set):
        trace.fail("planner", "grain_unexpressible", grain_check_site=_DEPENDENT_PART)
        return Unsupported(
            reason_code="grain_unexpressible",
            message="one part of the question needs another part's answer",
            detail=_refusal_detail(_DEPENDENT_PART, request, bundle),
        )
    return normalize_round(
        RoundResolution(
            request=request,
            planned_set=planned_set,
            bundle=bundle,
            continuation=None,
        )
    )


_DEPENDENT_PART = "companion_depends_on_answer"


def _refusal_detail(
    rule: str, request: BusinessQueryRequest, bundle: DefinitionBundle
) -> RefusalDetail:
    """The rule, and the record types outside and inside the viewer's reach, by label."""
    return RefusalDetail(
        rule=rule,
        outside_access=outside_access_labels(request.principal, bundle),
        available=available_access_labels(request.principal, bundle),
    )


def _explained_planner_refusal(
    module: Any, request: BusinessQueryRequest, refusal: Unsupported, trace: QueryTrace
) -> Unsupported:
    """A refusal the planner gave on its own carries the labels the coordinator offers."""
    bundle = module.load_bundle(request.principal, request.correlation_id)
    if not isinstance(bundle, DefinitionBundle):
        return refusal
    trace.fail("planner", refusal.reason_code, grain_check_site="planner_unsupported")
    return refusal.model_copy(
        update={"detail": _refusal_detail("planner_unsupported", request, bundle)}
    )


def continuation_stages(  # noqa: PLR0913
    module: Any,
    request: BusinessQueryRequest,
    scoped_plans: list[ScopedPlan],
    continuation: PlanContinuation,
    bundle: DefinitionBundle,
    evidence: BusinessQueryEvidenceContext | None,
) -> tuple[Any, Any] | Denied:
    """Prepare the resolve and execute stages a patched round runs with.

    Restores the stored scope onto the primary plan and fails closed when
    that scope no longer resolves to the caller's tiers, then keeps the
    patched answer id alive past value resolution and forwards the
    continuation identity to the presentation stage.
    """
    try:
        scoped_plans[0] = restore_stored_scope(
            scoped_plans[0],
            continuation.stored,
            principal=request.principal,
            bundle=bundle,
        )
    except ScopeDenied:
        return Denied(
            reason_code="policy_denied",
            message="business query tools are currently unavailable",
        )
    idempotency_root = evidence.idempotency_key if evidence is not None else request.correlation_id
    primary_aqid = answer_query_id_for(f"{idempotency_root}:patch:{continuation.subject}")
    scoped_plans[0] = scoped_plans[0].model_copy(update={"answer_query_id": primary_aqid})

    async def resolve_continued(*args: Any, **kwargs: Any) -> Any:
        resolved = await module.resolve_values(*args, **kwargs)
        if isinstance(resolved, tuple) and kwargs.get("ordinal", 0) == 0:
            resolved = resolved[0], resolved[1].model_copy(update={"answer_query_id": primary_aqid})
        return resolved

    execute_and_present = partial(
        module.execute_and_present,
        root_answer_query_id=continuation.subject,
        changes=continuation.changes,
        continuation_tier=continuation.tier,
    )
    return resolve_continued, execute_and_present
