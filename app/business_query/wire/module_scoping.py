"""Scoping, validation, bundle loading, and execution helpers for BusinessQueryModule."""

from __future__ import annotations

import logging
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
    Unsupported,
)
from app.business_query.plan import BusinessQueryPlan
from app.business_query.plan.value_resolver import (
    AuthorizedValueResolver,
    bind_exact_filter,
    find_resolver_lookups,
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
            trace.fail("planner", "member_not_found", members=list(visibility.unknown_members))
            return Unsupported(reason_code="member_not_found", message=UNSUPPORTED_MESSAGE)

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
        currency_outcome = native_currency_outcome(node, request, bundle, trace)
        if currency_outcome is not None:
            return currency_outcome
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
) -> BusinessQueryOutcome | UnsealedAdapterAnswer:
    last_unsupported_msg = "no adapter could execute this plan"
    for adapter in adapters:
        if evidence_required and not isinstance(adapter, EvidenceExecutionAdapter):
            return Incomplete(reason_code="adapter_invalid")
        try:
            execute = adapter.execute_with_evidence if evidence_required else adapter.execute
            result = await await_isolated_executor(
                lambda worker, execute=execute, scoped=scoped: partial(
                    execute, scoped, trace=worker
                ),
                trace,
                turn_budget,
            )
        except TimeoutError:
            return Incomplete(reason_code="timeout")
        except DeadlineExpiredError:
            raise
        except AdapterUnsupported as exc:
            last_unsupported_msg = str(exc) or last_unsupported_msg
            continue
        except PlanRefused as exc:
            return plan_refused_to_outcome(exc, trace)
        except ScopeDenied:
            return Denied(message=DENY_MESSAGE, reason_code="policy_denied")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "adapter failed correlation_id=%s error=%s",
                trace.correlation_id,
                type(exc).__name__,
                exc_info=True,
            )
            return Incomplete(reason_code="adapter_invalid")

        if isinstance(result, (Answered, UnsealedAdapterAnswer, Denied, Incomplete, Unsupported)):
            return result
        return Incomplete(reason_code="adapter_invalid")

    return Unsupported(
        reason_code="capability_disabled",
        message=last_unsupported_msg,
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
        disambiguation = None
        if result.candidates and 2 <= len(result.candidates) <= 5:
            from app.models.schemas import DisambiguationPayload

            disambiguation = DisambiguationPayload(
                term=lookup.raw_value,
                candidates=list(result.candidates),
            )
        return ClarificationRequired(
            question="that name matches more than one allowed value — which one did you mean?",
            continuation="resolver-ambiguous",
            resolver_query_id=resolver_query_id,
            disambiguation=disambiguation,
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

    rebound = bind_exact_filter(
        planned, lookup.member, result.canonical_values[0], path=lookup.path
    )
    prepared = validate_and_scope(rebound, request, bundle, scope_fn, record_plan=True, trace=trace)
    if not isinstance(prepared, ScopedPlan):
        return prepared
    return rebound, prepared
