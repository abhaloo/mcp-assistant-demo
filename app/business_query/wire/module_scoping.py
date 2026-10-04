"""Scoping, validation, bundle loading, and execution helpers for BusinessQueryModule."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from functools import partial
from typing import Any
from uuid import uuid4

from app.auth import Principal
from app.business_query.authorize.capability import (
    allowed_filter_values_valid,
)
from app.business_query.authorize.preconditions import (
    check_business_date_requirement,
    check_member_visibility,
)
from app.business_query.authorize.scoping import (
    ScopeDenied,
    ScopedPlan,
    apply_owner_hint_scope,
    bind_scope_context,
)
from app.business_query.cube.transport import (
    CubeBadResponse,
    CubeCancelled,
    CubeUnavailable,
    accepts_cancel_token,
)
from app.business_query.definitions import (
    BundleSelectionError,
    BundleValidationError,
    DefinitionBundle,
    InvalidBundleIndexError,
)
from app.business_query.outcomes import (
    AdapterUnsupported,
    Answered,
    BusinessQueryOutcome,
    ClarificationRequired,
    Denied,
    Incomplete,
    PlanRefused,
    RefusalDetail,
    Unsupported,
)
from app.business_query.plan import BusinessQueryPlan
from app.business_query.plan.value_resolver import (
    AuthorizedValueResolver,
    bind_exact_filter,
    clarification_choice_rewrite,
    find_resolver_lookups,
    lookup_identity,
    resolver_query_id_for,
)
from app.business_query.ports import (
    BusinessProgressSink,
    EvidenceExecutionAdapter,
    ExecutionAdapter,
    PlanStore,
    ProgressStage,
)
from app.business_query.seal.events import answer_query_id_for
from app.business_query.seal.evidence import (
    BusinessQueryEvidenceContext,
    UnsealedAdapterAnswer,
)
from app.business_query.wire.outcome_rules import (
    native_currency_outcome,
    plan_refused_to_outcome,
    raw_time_grouping_outcome,
    required_filters_present,
)
from app.business_query.wire.request import (
    BundleResolver,
    BusinessQueryRequest,
    Presenter,
    ScopeFn,
)
from app.business_query.wire.trace import QueryTrace
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import TurnBudget
from app.rag.provenance.record_links import record_href

logger = logging.getLogger(__name__)

DENY_MESSAGE = "business query tools are currently unavailable"
UNSUPPORTED_MESSAGE = "that isn't available to ask here"


def emit_progress(
    progress: BusinessProgressSink | None,
    stage: ProgressStage,
    *,
    ordinal: int | None = None,
    of: int | None = None,
    subject: str | None = None,
) -> None:
    if progress is None:
        return
    progress.emit(stage, ordinal=ordinal, of=of, subject=subject)


def load_bundle(
    bundle_resolver: BundleResolver,
    principal: Principal,
    correlation_id: str,
) -> DefinitionBundle | Denied | Incomplete:
    manifest_hash = principal.manifest_hash
    if not manifest_hash:
        return Denied(message=DENY_MESSAGE, reason_code="policy_denied")
    try:
        return bundle_resolver(manifest_hash)
    except BundleSelectionError:
        return Denied(message=DENY_MESSAGE, reason_code="policy_denied")
    except (
        InvalidBundleIndexError,
        BundleValidationError,
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
    ) as exc:
        logger.warning(
            "bundle load failed correlation_id=%s error=%s", correlation_id, type(exc).__name__
        )
        return Incomplete(reason_code="adapter_invalid")


def validate_and_scope(
    planned: BusinessQueryPlan,
    request: BusinessQueryRequest,
    bundle: DefinitionBundle,
    scope_fn: ScopeFn,
    *,
    record_plan: bool,
    trace: QueryTrace,
) -> ScopedPlan | Denied | Incomplete | Unsupported | ClarificationRequired:
    from app.business_query.plan import iter_plan_nodes, replace_plan_at_path
    from app.business_query.plan.detail_family import (
        canonicalize_attribute_predicates,
        canonicalize_detail_selections,
    )

    if request.question is not None:
        from app.business_query.compile.detail_engine import (
            align_detail_selections_to_explicit_question,
            expand_all_visible_detail_selections,
        )

        planned = expand_all_visible_detail_selections(
            request.question, planned, principal=request.principal, bundle=bundle
        )
        planned = align_detail_selections_to_explicit_question(
            request.question, planned, bundle=bundle
        )

    for path, node in list(iter_plan_nodes(planned)):
        try:
            canonical_node = canonicalize_detail_selections(node, bundle=bundle)
            canonical_node = canonicalize_attribute_predicates(canonical_node, bundle=bundle)
            planned = replace_plan_at_path(planned, path, canonical_node)
        except PlanRefused as exc:
            if record_plan:
                trace.record_plan(planned.model_dump(mode="json"))
            return plan_refused_to_outcome(exc, trace)

    # Visibility is checked for every node (root plan and each derived set)
    # BEFORE the plan is ever recorded on the trace. A denied, unauthorized
    # plan must never reach telemetry (ADR 0022) -- only a member-not-found
    # or fully authorized plan may be recorded.
    for _path, node in iter_plan_nodes(planned):
        visibility = check_member_visibility(node, request.principal, bundle)
        if visibility is not None:
            if visibility.kind == "unauthorized":
                return Denied(message=DENY_MESSAGE, reason_code="policy_denied")
            if record_plan:
                trace.record_plan(planned.model_dump(mode="json"))
            trace.fail(
                "planner",
                "member_not_found",
                members=list(visibility.unknown_members),
                grain_check_site="unknown_member",
            )
            return Unsupported(
                reason_code="member_not_found",
                message=UNSUPPORTED_MESSAGE,
                detail=RefusalDetail(
                    rule="unknown_member", members=list(visibility.unknown_members)
                ),
            )

    if record_plan:
        trace.record_plan(planned.model_dump(mode="json"))

    for _path, node in iter_plan_nodes(planned):
        if check_business_date_requirement(node, bundle, request.business_date) is not None:
            return Incomplete(reason_code="adapter_invalid")
        if not allowed_filter_values_valid(node, bundle):
            trace.fail("module", "grain_unexpressible", grain_check_site="filter_value_allowlist")
            return Unsupported(
                reason_code="grain_unexpressible", message="plan cannot be executed safely"
            )
        refusal = native_currency_outcome(
            node, request, bundle, trace
        ) or raw_time_grouping_outcome(node, bundle, trace)
        if refusal is not None:
            return refusal
        if not required_filters_present(node, bundle):
            trace.fail("module", "grain_unexpressible", grain_check_site="required_filters_missing")
            return Unsupported(
                reason_code="grain_unexpressible", message="plan cannot be executed safely"
            )

    try:
        from app.business_query.wire.answer_finalization import assert_set_context_fits

        assert_set_context_fits(planned, request.max_answer_chars)

        scoped = bind_scope_context(
            scope_fn(planned, request.principal, bundle),
            principal=request.principal,
            bundle_hash=bundle.content_hash,
            business_date=request.business_date,
            response_policy=request.response_policy,
        )
        if request.owner_hint is not None:
            if request.owner_hint.resource_type not in scoped.resources:
                trace.fail(
                    "module",
                    "grain_unexpressible",
                    grain_check_site="owner_hint_unexpressible",
                )
                return Unsupported(
                    reason_code="grain_unexpressible", message="plan cannot be executed safely"
                )
            scoped = apply_owner_hint_scope(scoped, request.owner_hint, bundle)

        from app.business_query.plan import PlanFilter, iter_filter_leaves

        # Key-compatibility itself (assert_set_key_compatible) is not
        # re-checked here: scope_fn (apply_role_scope by default) already ran
        # it while building `scoped`, and it is the ONLY check the pagination
        # reauthorization path (page_executor.py) reaches -- duplicating it
        # here made it verifiably redundant on that shared default path.
        # This membership-consistency check stays: it is real defense for a
        # non-default injected scope_fn that might not enforce it.
        derived_by_id = {d.id: d for d in scoped.derived}
        if len(derived_by_id) != len(planned.derived_sets):
            raise ScopeDenied()
        for d in planned.derived_sets:
            if d.id not in derived_by_id:
                raise ScopeDenied()

        for leaf in iter_filter_leaves(planned.filters):
            if isinstance(leaf, PlanFilter) and leaf.operator in {"in_set", "not_in_set"}:
                set_id = str(leaf.values[0])
                if derived_by_id.get(set_id) is None:
                    raise ScopeDenied()

        return scoped
    except PlanRefused as exc:
        return plan_refused_to_outcome(exc, trace)
    except ScopeDenied:
        return Denied(message=DENY_MESSAGE, reason_code="policy_denied")


async def execute_page_cursor(
    request: BusinessQueryRequest,
    evidence: BusinessQueryEvidenceContext | None,
    turn_budget: TurnBudget,
    *,
    plan_store: PlanStore | None,
    adapters: Sequence[ExecutionAdapter],
    pagination_secret: str | None,
    bundle_resolver: BundleResolver,
    scope_fn: ScopeFn,
    presenter: Presenter,
    evidence_sealer: Callable[..., Any] | None,
    executor: Any,
    step_timeout_seconds: float,
    terminal_reserve_seconds: float,
) -> BusinessQueryOutcome:
    if plan_store is None:
        return Incomplete(reason_code="adapter_invalid")
    from app.business_query.compile.pagination import ResultPageExecutor
    from app.business_query.wire.answer_finalization import (
        assert_set_context_fits,
        finalize_answer_text,
    )

    page_executor = ResultPageExecutor(
        plan_store=plan_store,
        assert_set_context_fits=assert_set_context_fits,
        finalize_answer_text=finalize_answer_text,
        adapters=list(adapters),
        secret=pagination_secret,
        bundle_resolver=bundle_resolver,
        scope_fn=scope_fn,
        presenter=presenter,
        project_id=evidence.project_id if evidence else "default",
        evidence_sealer=evidence_sealer,
        receipt_id_factory=lambda c, i: (
            answer_query_id_for(f"{i}:{c.offset_row_count}") if i else uuid4().hex
        ),
        turn_budget=turn_budget,
        executor=executor,
        step_timeout_seconds=step_timeout_seconds,
        terminal_reserve_seconds=terminal_reserve_seconds,
        max_answer_chars=request.max_answer_chars,
    )
    return await page_executor.execute(
        request.page_cursor,
        request.principal,
        idempotency_key=evidence.idempotency_key if evidence else None,
    )


def _handle_cube_unavailable(
    adapter: ExecutionAdapter,
    exc: CubeUnavailable,
    trace: QueryTrace,
    can_fallback: bool,
) -> tuple[BusinessQueryOutcome | None, str | None]:
    logger.warning(
        "adapter unavailable correlation_id=%s adapter=%s error=%s",
        trace.correlation_id,
        type(adapter).__name__,
        str(exc),
    )
    if can_fallback:
        return None, None
    return Incomplete(reason_code="unavailable"), None


async def _execute_adapter(
    coro: Any,
    adapter: ExecutionAdapter,
    trace: QueryTrace,
    turn_budget: TurnBudget,
    fallback_config: tuple[bool, float],
) -> tuple[BusinessQueryOutcome | UnsealedAdapterAnswer | None, str | None]:
    try:
        result = await coro
    except DeadlineExpiredError:
        raise
    except (TimeoutError, CubeCancelled) as exc:
        # CubeTimeout is a TimeoutError: the adapter's own deadline fired.
        reason = "cancelled" if isinstance(exc, CubeCancelled) else "timeout"
        return Incomplete(reason_code=reason), None
    except AdapterUnsupported as exc:
        return None, str(exc) or None
    except CubeUnavailable as exc:
        fallback_enabled, min_remaining = fallback_config
        can_fallback = fallback_enabled and turn_budget.remaining_seconds > min_remaining
        return _handle_cube_unavailable(adapter, exc, trace, can_fallback)
    except CubeBadResponse as exc:
        logger.warning(
            "adapter bad response correlation_id=%s error=%s",
            trace.correlation_id,
            str(exc),
        )
        return Incomplete(reason_code="adapter_invalid"), None
    except PlanRefused as exc:
        return plan_refused_to_outcome(exc, trace), None
    except ScopeDenied:
        return Denied(message=DENY_MESSAGE, reason_code="policy_denied"), None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "adapter failed correlation_id=%s error=%s",
            trace.correlation_id,
            type(exc).__name__,
            exc_info=True,
        )
        return Incomplete(reason_code="adapter_invalid"), None

    if isinstance(result, (Answered, UnsealedAdapterAnswer, Denied, Incomplete, Unsupported)):
        return result, None
    return Incomplete(reason_code="adapter_invalid"), None


def _invoke_adapter(
    execute: Callable[..., Any],
    scoped: ScopedPlan,
    worker: QueryTrace,
    cancel_token: threading.Event,
) -> Any:
    if accepts_cancel_token(execute):
        return execute(scoped, trace=worker, cancel_token=cancel_token)
    return execute(scoped, trace=worker)


async def run_adapters(
    adapters: Sequence[ExecutionAdapter],
    scoped: ScopedPlan,
    *,
    evidence_required: bool,
    trace: QueryTrace,
    turn_budget: TurnBudget,
    await_isolated_executor: Callable[
        [Callable[[QueryTrace], Callable[[], Any]], QueryTrace, TurnBudget], Any
    ],
    fallback_on_unavailable: bool = True,
    fallback_min_remaining_seconds: float = 3.0,
) -> BusinessQueryOutcome | UnsealedAdapterAnswer:
    last_unsupported_msg = "no adapter could execute this plan"
    fallback_config = (fallback_on_unavailable, fallback_min_remaining_seconds)
    for adapter in adapters:
        if turn_budget.cancel_token.is_set():
            return Incomplete(reason_code="cancelled")
        if evidence_required and not isinstance(adapter, EvidenceExecutionAdapter):
            return Incomplete(reason_code="adapter_invalid")
        execute = adapter.execute_with_evidence if evidence_required else adapter.execute
        coro = await_isolated_executor(
            lambda worker, execute=execute, scoped=scoped: partial(
                _invoke_adapter, execute, scoped, worker, turn_budget.cancel_token
            ),
            trace,
            turn_budget,
        )
        outcome, unsupported_msg = await _execute_adapter(
            coro,
            adapter,
            trace,
            turn_budget,
            fallback_config,
        )
        if outcome is not None:
            return outcome
        if unsupported_msg is not None:
            last_unsupported_msg = unsupported_msg

    return Unsupported(
        reason_code="capability_disabled",
        message=last_unsupported_msg,
    )


def _clarification_for_ambiguous(  # noqa: PLR0913
    result: Any,
    lookup: Any,
    question: str,
    resolver_query_id: str,
    bundle: DefinitionBundle,
    principal: Principal,
) -> ClarificationRequired:
    from app.business_query.plan.value_resolver.contract import CARD_PICK_CAP, CARD_PICK_MIN
    from app.models.schemas import DisambiguationPayload

    disambiguation = None
    choices: list[dict[str, str | None]] = []
    candidates = result.candidates
    if candidates and CARD_PICK_MIN <= len(candidates) <= CARD_PICK_CAP:
        disambiguation = DisambiguationPayload(
            term=lookup.raw_value,
            candidates=list(candidates),
        )
        choices = [
            {
                "id": cand.id,
                "label": cand.label,
                "rewrite": clarification_choice_rewrite(question, lookup.raw_value, cand.label),
                "detail": None,
                "href": (
                    record_href(bundle, principal, cand.resource_type, int(cand.id))
                    if cand.id.isdigit()
                    else None
                ),
            }
            for cand in candidates
        ]
    return ClarificationRequired(
        question="that name matches more than one allowed value — which one did you mean?",
        continuation="resolver-ambiguous",
        resolver_query_id=resolver_query_id,
        disambiguation=disambiguation,
        choices=choices,
        allow_free_text=True,
    )


def _bind_resolved_identity(
    planned: BusinessQueryPlan,
    lookup: Any,
    result: Any,
    bundle: DefinitionBundle,
) -> BusinessQueryPlan:
    _sql_col, identity_member = lookup_identity(lookup, bundle)
    canonical: str | int = result.canonical_values[0]
    if identity_member is not None:
        identity_dim = next(d for d in bundle.dimensions if d.name == identity_member)
        if identity_dim.type == "number":
            canonical = int(result.canonical_values[0])
    return bind_exact_filter(
        planned,
        lookup.member,
        canonical,
        path=lookup.path,
        bind_member=identity_member,
    )


async def resolve_values(
    planned: BusinessQueryPlan,
    scoped: ScopedPlan,
    request: BusinessQueryRequest,
    bundle: DefinitionBundle,
    *,
    value_resolver: AuthorizedValueResolver | None,
    scope_fn: ScopeFn,
    evidence: BusinessQueryEvidenceContext | None,
    trace: QueryTrace,
    progress: BusinessProgressSink | None = None,
    ordinal: int = 0,
    of: int = 1,
    turn_budget: TurnBudget,
    await_isolated_executor: Callable[..., Any],
    commit_resolver_started_fn: Callable[..., Any],
) -> (
    tuple[BusinessQueryPlan, ScopedPlan] | ClarificationRequired | Unsupported | Incomplete | Denied
):
    if evidence is not None:
        aqid_key = (
            evidence.idempotency_key
            if ordinal == 0
            else f"{evidence.idempotency_key}:sub:{ordinal}"
        )
        scoped = scoped.model_copy(update={"answer_query_id": answer_query_id_for(aqid_key)})
    lookups = find_resolver_lookups(planned, bundle)
    if not lookups:
        return planned, scoped
    emit_progress(
        progress,
        "finding_record",
        ordinal=ordinal if of > 1 else None,
        of=of if of > 1 else None,
    )
    if len(lookups) != 1:
        trace.fail("module", "grain_unexpressible", grain_check_site="resolver_lookup")
        return Unsupported(
            reason_code="grain_unexpressible", message="plan cannot be executed safely"
        )
    if value_resolver is None:
        return Incomplete(reason_code="adapter_invalid")

    lookup = lookups[0]
    resolver_query_id = resolver_query_id_for(
        f"{request.correlation_id}:resolver"
        if ordinal == 0
        else f"{request.correlation_id}:resolver:sub:{ordinal}"
    )
    trace.resolver_query_id = resolver_query_id
    trace.resolver_started = True
    trace.resolver_value_type = lookup.value_type

    started = await commit_resolver_started_fn(
        resolver_query_id=resolver_query_id,
        request=request,
        value_type=lookup.value_type,
        evidence=evidence,
    )
    if started is not None:
        return started.model_copy(update={"resolver_query_id": resolver_query_id})

    target_scoped = scoped
    if lookup.path:
        derived_by_id = {d.id: d for d in scoped.derived}
        if lookup.path[0] not in derived_by_id:
            return Denied(message=DENY_MESSAGE, reason_code="policy_denied")
        target_scoped = derived_by_id[lookup.path[0]].scoped

    try:
        result = await await_isolated_executor(
            lambda worker: partial(
                value_resolver.resolve,
                lookup,
                target_scoped,
                bundle,
                trace=worker,
            ),
            trace,
            turn_budget,
        )
    except TimeoutError:
        trace.resolver_disposition = "none"
        return Incomplete(reason_code="timeout", resolver_query_id=resolver_query_id)
    except DeadlineExpiredError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "value resolver failed correlation_id=%s error=%s",
            request.correlation_id,
            type(exc).__name__,
        )
        return Incomplete(reason_code="adapter_invalid", resolver_query_id=resolver_query_id)

    trace.resolver_disposition = result.disposition
    trace.resolver_match_count = result.match_count
    trace.resolver_version = result.resolver_version

    if result.disposition == "ambiguous":
        return _clarification_for_ambiguous(
            result, lookup, request.question or "", resolver_query_id, bundle, request.principal
        )
    if result.disposition == "none":
        # The member resolved and was authorized; only its VALUE matched
        # nothing, so the refusal names the value, not the field.
        trace.fail("resolver", "value_not_found", members=[lookup.member])
        return Unsupported(
            reason_code="value_not_found",
            message="nothing matches that name or number",
            resolver_query_id=resolver_query_id,
        )

    rebound = _bind_resolved_identity(planned, lookup, result, bundle)
    prepared = validate_and_scope(rebound, request, bundle, scope_fn, record_plan=True, trace=trace)
    if not isinstance(prepared, ScopedPlan):
        return prepared
    return rebound, prepared
