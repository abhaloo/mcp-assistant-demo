"""BusinessQueryModule — public assembly seam (ADR 0047)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from functools import partial
from typing import Any

from app.auth import Principal
from app.business_query.authorize.capability import (
    capability_card,
)
from app.business_query.authorize.scoping import (
    ScopedPlan,
    apply_role_scope,
)
from app.business_query.budget import INTERACTIVE_BUDGET_POLICY, BusinessQueryBudgetPolicy
from app.business_query.definitions import (
    DefinitionBundle,
    bundle_for_manifest,
)
from app.business_query.outcomes import (
    Answered,
    BusinessQueryOutcome,
    ClarificationRequired,
    Denied,
    Incomplete,
    Unsupported,
)
from app.business_query.plan import BusinessQueryPlan
from app.business_query.plan.planned_set import PlannedQuerySet
from app.business_query.plan.value_resolver import (
    AuthorizedValueResolver,
    ResolvableType,
)
from app.business_query.ports import (
    BusinessProgressSink as BusinessProgressSink,
)
from app.business_query.ports import (
    EvidenceExecutionAdapter as EvidenceExecutionAdapter,
)
from app.business_query.ports import (
    ExecutionAdapter as ExecutionAdapter,
)
from app.business_query.ports import (
    PlannerAdapter as PlannerAdapter,
)
from app.business_query.ports import (
    PlanStore as PlanStore,
)
from app.business_query.ports import (
    ProgressStage as ProgressStage,
)
from app.business_query.ports import (
    QueryRecordWritePort as QueryRecordWritePort,
)
from app.business_query.seal.evidence import (
    BusinessQueryEvidenceContext as BusinessQueryEvidenceContext,
)
from app.business_query.seal.evidence import (
    BusinessQueryEvidencePorts as BusinessQueryEvidencePorts,
)
from app.business_query.seal.evidence import (
    UnsealedAdapterAnswer as UnsealedAdapterAnswer,
)
from app.business_query.seal.evidence import (
    commit_resolver_started,
    seal_execution_answer,
    seal_page_execution_evidence,
)
from app.business_query.wire.answer_finalization import (
    finalize_answer_text,
)
from app.business_query.wire.comparison_presenter import present_for_plan
from app.business_query.wire.module_scoping import (
    emit_progress,
    execute_page_cursor,
    load_bundle,
    resolve_values,
    run_adapters,
    validate_and_scope,
)
from app.business_query.wire.planned_execution import (
    AuthorizedSetExecution,
    compose_answers,
    execute_authorized_set,
)
from app.business_query.wire.planning_round import (
    PLANNER_TIMEOUT_CONTINUATION as PLANNER_TIMEOUT_CONTINUATION,
)
from app.business_query.wire.planning_round import (
    run_planner_round,
    shape_question,
)
from app.business_query.wire.request import (
    BundleResolver as BundleResolver,
)
from app.business_query.wire.request import (
    BusinessQueryOwnerHint as BusinessQueryOwnerHint,
)
from app.business_query.wire.request import (
    BusinessQueryRequest as BusinessQueryRequest,
)
from app.business_query.wire.request import (
    Presenter as Presenter,
)
from app.business_query.wire.request import (
    ScopeFn as ScopeFn,
)
from app.business_query.wire.request_lifecycle import BqRequestScope
from app.business_query.wire.result_presentation import step_subject
from app.business_query.wire.tool_descriptor import BUSINESS_QUERY_PLAN_TOOL
from app.business_query.wire.trace import QueryTrace
from app.business_query.wire.trace_cache import TraceSnapshotCache
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import (
    UNBOUNDED_BUDGET,
    TurnBudget,
    await_with_budget,
    run_blocking_with_budget,
)

logger = logging.getLogger(__name__)
_TRACE_SNAPSHOT_LIMIT = 32


class BusinessQueryModule:
    """One public ``query`` seam; adapters are injected (scripted in tests)."""

    def __init__(
        self,
        *,
        planner: PlannerAdapter,
        adapters: Sequence[ExecutionAdapter],
        bundle_resolver: BundleResolver | None = None,
        scope_fn: ScopeFn | None = None,
        presenter: Presenter | None = None,
        step_timeout_seconds: float = 10.0,
        planner_step_ceiling_seconds: float | None = None,
        budget_policy: BusinessQueryBudgetPolicy = INTERACTIVE_BUDGET_POLICY,
        trace: QueryTrace | None = None,
        evidence_ports: BusinessQueryEvidencePorts | None = None,
        value_resolver: AuthorizedValueResolver | None = None,
        executor: ThreadPoolExecutor | None = None,
        default_business_date: date | None = None,
        plan_store: PlanStore | None = None,
        pagination_secret: str | None = None,
        mint_page_cursor: bool = True,
        query_record_writer: QueryRecordWritePort | None = None,
    ) -> None:
        # Per-step ceiling: applied independently at each budgeted wait site
        # (planner round, presenter, _resolve_values, _run_adapters,
        # _commit_resolver_started, _seal_answer). A 10s value bounds each
        # step, not the whole operation.
        self._planner = planner
        self._adapters = list(adapters)
        self._bundle_resolver = bundle_resolver or bundle_for_manifest
        self._scope_fn = scope_fn or apply_role_scope
        self._presenter = presenter or present_for_plan
        self._step_timeout_seconds = step_timeout_seconds
        # Interactive transports inject a tighter planner ceiling so a model
        # stall refuses cleanly inside their turn deadline; eval and canary
        # builders leave it None and keep the full route budget.
        self._planner_step_ceiling_seconds = planner_step_ceiling_seconds
        self._budget_policy = budget_policy
        # Injection-only, never self-constructed (build_module always
        # injects the shared pool); None falls back to asyncio's default.
        self._executor = executor
        self._default_business_date = default_business_date
        self._plan_store = plan_store
        self._pagination_secret = pagination_secret
        self._mint_page_cursor = mint_page_cursor
        self._query_record_writer = query_record_writer
        self._trace = trace or QueryTrace()
        self._injected_trace = trace
        self._trace_cache = TraceSnapshotCache(limit=_TRACE_SNAPSHOT_LIMIT)
        self._evidence_ports = evidence_ports
        self._value_resolver = value_resolver
        self._active_scope: BqRequestScope | None = None

    @property
    def query_record_writer(self) -> QueryRecordWritePort | None:
        """Ask persistence reads this; composition injects via the constructor."""
        return self._query_record_writer

    @property
    def budget_policy(self) -> BusinessQueryBudgetPolicy:
        return self._budget_policy

    def trace_for(self, correlation_id: str) -> QueryTrace:
        return self._trace_cache.snapshot(correlation_id)

    def _bind_request_trace(self, request: BusinessQueryRequest) -> QueryTrace:
        trace = QueryTrace(correlation_id=request.correlation_id)
        if self._injected_trace is not None:
            trace.capture_planner_payload = self._injected_trace.capture_planner_payload
        self._trace_cache.bind(request.correlation_id, trace)
        return trace

    def _finalize_request_trace(self, correlation_id: str, request_trace: QueryTrace) -> None:
        if self._injected_trace is not None:
            request_trace.copy_into(self._injected_trace)
        self._trace_cache.record(correlation_id, request_trace)

    @asynccontextmanager
    async def request_scope(
        self,
        request: BusinessQueryRequest,
        *,
        turn_budget: TurnBudget,
    ) -> AsyncIterator[BqRequestScope]:
        """Bind one request trace for prepare and execute within the same turn."""
        del turn_budget
        request_trace = self._bind_request_trace(request)
        scope = BqRequestScope(
            request=request,
            trace=request_trace,
            _finalize_trace=self._finalize_request_trace,
        )
        self._active_scope = scope
        try:
            yield scope
        finally:
            scope.finalize_if_needed()
            self._active_scope = None

    async def _await_isolated_executor(
        self,
        build_job: Callable[[QueryTrace], Callable[[], Any]],
        trace: QueryTrace,
        turn_budget: TurnBudget,
    ) -> Any:
        """Run a blocking job against a worker-local trace copy.

        Cancelling the await does not stop the worker thread. Copy the
        worker trace into the request trace only after an in-budget result.
        """
        worker_trace = QueryTrace()
        trace.copy_into(worker_trace)

        def operation() -> Any:
            return build_job(worker_trace)()

        result = await run_blocking_with_budget(
            operation,
            turn_budget,
            executor=self._executor,
            ceiling_seconds=self._step_timeout_seconds,
            reserve_seconds=self._budget_policy.terminal_reserve_seconds,
        )
        worker_trace.copy_into(trace)
        return result

    async def query(
        self,
        request: BusinessQueryRequest,
        *,
        progress: BusinessProgressSink | None = None,
        evidence: BusinessQueryEvidenceContext | None = None,
        turn_budget: TurnBudget = UNBOUNDED_BUDGET,
    ) -> BusinessQueryOutcome:
        async def body(
            bound_request: BusinessQueryRequest, trace: QueryTrace
        ) -> BusinessQueryOutcome:
            return await self._query(
                bound_request,
                progress=progress,
                evidence=evidence,
                trace=trace,
                turn_budget=turn_budget,
            )

        return await self.bounded(request, turn_budget=turn_budget, body=body)

    async def bounded(
        self,
        request: BusinessQueryRequest,
        *,
        turn_budget: TurnBudget,
        body: Callable[[BusinessQueryRequest, QueryTrace], Awaitable[BusinessQueryOutcome]],
    ) -> BusinessQueryOutcome:
        """Run ``body`` under the request trace and the typed-timeout mapping."""
        try:
            async with self.request_scope(request, turn_budget=turn_budget) as scope:
                outcome = await body(scope.request, scope.trace)
                return scope.finish(outcome)
        except DeadlineExpiredError:
            return Incomplete(reason_code="timeout")

    async def execute_planned_set(  # noqa: PLR0913
        self,
        request: BusinessQueryRequest,
        planned_set: PlannedQuerySet,
        *,
        progress: BusinessProgressSink | None = None,
        evidence: BusinessQueryEvidenceContext | None = None,
        trace: QueryTrace,
        turn_budget: TurnBudget,
    ) -> BusinessQueryOutcome:
        """Authorize and execute a prepared plan set without replanning."""
        bundle_outcome = self.load_bundle(request.principal, request.correlation_id)
        if isinstance(bundle_outcome, (Denied, Incomplete)):
            return bundle_outcome
        bundle = bundle_outcome
        active_scope = self._active_scope
        if (
            active_scope is not None
            and active_scope.bound_bundle_hash is not None
            and bundle.content_hash != active_scope.bound_bundle_hash
        ):
            return Incomplete(reason_code="adapter_invalid")
        scoped_plans = self.authorize_set(
            request,
            planned_set,
            bundle,
            progress=progress,
            trace=trace,
        )
        if not isinstance(scoped_plans, list):
            return scoped_plans
        return await execute_authorized_set(
            AuthorizedSetExecution(
                request=request,
                scoped_plans=scoped_plans,
                bundle=bundle,
                progress=progress,
                evidence=evidence,
                trace=trace,
                turn_budget=turn_budget,
            ),
            resolve_values=self.resolve_values,
            execute_and_present=self.execute_and_present,
        )

    async def _query(
        self,
        request: BusinessQueryRequest,
        *,
        progress: BusinessProgressSink | None = None,
        evidence: BusinessQueryEvidenceContext | None = None,
        trace: QueryTrace,
        turn_budget: TurnBudget = UNBOUNDED_BUDGET,
    ) -> BusinessQueryOutcome:
        if self._evidence_ports is not None and evidence is None:
            return Incomplete(reason_code="adapter_invalid")
        if request.page_cursor is not None:
            turn_budget.check_not_expired()
            return await self._execute_page_cursor(request, evidence, turn_budget)
        plan_res = await self.plan_round(
            request, progress=progress, trace=trace, turn_budget=turn_budget
        )
        if not isinstance(plan_res, tuple):
            return plan_res
        request, planned_set, bundle = plan_res
        if self._active_scope is not None:
            self._active_scope.bind_plan_bundle(bundle)
        return await self.execute_planned_set(
            request,
            planned_set,
            progress=progress,
            evidence=evidence,
            trace=trace,
            turn_budget=turn_budget,
        )

    compose_answers = staticmethod(compose_answers)

    async def _execute_page_cursor(
        self,
        request: BusinessQueryRequest,
        evidence: BusinessQueryEvidenceContext | None,
        turn_budget: TurnBudget,
    ) -> BusinessQueryOutcome:
        return await execute_page_cursor(
            request,
            evidence,
            turn_budget,
            plan_store=self._plan_store,
            adapters=self._adapters,
            pagination_secret=self._pagination_secret,
            bundle_resolver=self._bundle_resolver,
            scope_fn=self._scope_fn,
            presenter=self._presenter,
            evidence_sealer=(
                partial(
                    seal_page_execution_evidence,
                    self._evidence_ports,
                    evidence=evidence,
                    step_timeout_seconds=self._step_timeout_seconds,
                )
                if evidence
                else None
            ),
            executor=self._executor,
            step_timeout_seconds=self._step_timeout_seconds,
            terminal_reserve_seconds=self._budget_policy.terminal_reserve_seconds,
        )

    async def plan_round(
        self,
        request: BusinessQueryRequest,
        *,
        progress: BusinessProgressSink | None = None,
        trace: QueryTrace,
        turn_budget: TurnBudget,
    ) -> (
        tuple[BusinessQueryRequest, PlannedQuerySet, DefinitionBundle]
        | ClarificationRequired
        | Denied
        | Incomplete
        | Unsupported
    ):
        if request.business_date is None and self._default_business_date is not None:
            request = request.model_copy(update={"business_date": self._default_business_date})
        bundle_outcome = self.load_bundle(request.principal, request.correlation_id)
        if isinstance(bundle_outcome, (Denied, Incomplete)):
            return bundle_outcome
        bundle = bundle_outcome

        emit_progress(progress, "understand")
        shaped = shape_question(request, bundle)
        if isinstance(shaped, ClarificationRequired):
            return shaped
        request = shaped

        emit_progress(progress, "planning")
        card = capability_card(request.principal, bundle)
        planner_timeout = (
            min(self._step_timeout_seconds, self._planner_step_ceiling_seconds)
            if self._planner_step_ceiling_seconds is not None
            else self._step_timeout_seconds
        )
        planned = await run_planner_round(
            planner=self._planner,
            request=request,
            card=card,
            timeout=planner_timeout,
            trace=trace,
            turn_budget=turn_budget,
            reserve_seconds=self._budget_policy.planning_reserve_seconds,
            progress=progress,
        )
        if isinstance(planned, ClarificationRequired):
            return planned
        if isinstance(planned, (Unsupported, Incomplete, Denied)):
            reason = getattr(planned, "reason_code", None)
            if reason is not None and trace.failure_layer is None:
                trace.fail("planner", reason)
            return planned
        if isinstance(planned, PlannedQuerySet):
            planned_set = planned
        elif isinstance(planned, BusinessQueryPlan):
            planned_set = PlannedQuerySet(primary=planned)
        else:
            return Incomplete(reason_code="adapter_invalid")
        return request, planned_set, bundle

    def authorize_set(
        self,
        request: BusinessQueryRequest,
        planned_set: PlannedQuerySet,
        bundle: DefinitionBundle,
        *,
        progress: BusinessProgressSink | None = None,
        trace: QueryTrace,
    ) -> list[ScopedPlan] | Denied | Incomplete | Unsupported | ClarificationRequired:
        members = (planned_set.primary, *planned_set.companions)
        emit_progress(progress, "authorize")
        scoped_plans: list[ScopedPlan] = []
        for member in members:
            BUSINESS_QUERY_PLAN_TOOL.assert_dispatchable()
            member = member.model_copy(update={"limit": min(member.limit, request.max_rows)})
            prepared = validate_and_scope(
                member,
                request,
                bundle,
                self._scope_fn,
                record_plan=True,
                trace=trace,
            )
            if not isinstance(prepared, ScopedPlan):
                return prepared
            scoped_plans.append(prepared)
        emit_progress(progress, "authorized")
        return scoped_plans

    async def execute_and_present(
        self,
        request: BusinessQueryRequest,
        scoped: ScopedPlan,
        *,
        progress: BusinessProgressSink | None,
        evidence: BusinessQueryEvidenceContext | None,
        trace: QueryTrace,
        ordinal: int = 0,
        of: int = 1,
        turn_budget: TurnBudget,
    ) -> BusinessQueryOutcome:
        tick_ordinal = ordinal if of > 1 else None
        tick_of = of if of > 1 else None
        emit_progress(
            progress,
            "querying",
            ordinal=tick_ordinal,
            of=tick_of,
            subject=step_subject(scoped.plan),
        )
        adapter_outcome = await run_adapters(
            self._adapters,
            scoped,
            evidence_required=self._evidence_ports is not None,
            trace=trace,
            turn_budget=turn_budget,
            await_isolated_executor=self._await_isolated_executor,
        )
        if isinstance(adapter_outcome, UnsealedAdapterAnswer):
            assert evidence is not None and self._evidence_ports is not None
            emit_progress(progress, "sealing", ordinal=tick_ordinal, of=tick_of)
            outcome = await await_with_budget(
                lambda: seal_execution_answer(
                    self._evidence_ports,
                    adapter_outcome,
                    scoped=scoped,
                    principal=request.principal,
                    correlation_id=request.correlation_id,
                    evidence=evidence,
                    resolver_query_id=trace.resolver_query_id,
                    step_timeout_seconds=self._step_timeout_seconds,
                    plan_store=self._plan_store,
                    pagination_secret=self._pagination_secret,
                    mint_page_cursor=self._mint_page_cursor,
                ),
                turn_budget,
                ceiling_seconds=self._step_timeout_seconds,
                reserve_seconds=self._budget_policy.terminal_reserve_seconds,
            )
        else:
            outcome = adapter_outcome
        emit_progress(progress, "answering", ordinal=tick_ordinal, of=tick_of)

        if isinstance(outcome, Answered):
            try:
                text = await run_blocking_with_budget(
                    lambda: self._presenter(scoped.plan, outcome),
                    turn_budget,
                    executor=self._executor,
                    ceiling_seconds=self._step_timeout_seconds,
                    reserve_seconds=self._budget_policy.terminal_reserve_seconds,
                )
            except TimeoutError:
                text = outcome.answer_text
            except DeadlineExpiredError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "presenter failed correlation_id=%s error=%s",
                    request.correlation_id,
                    type(exc).__name__,
                )
                if not scoped.plan.derived_sets:
                    # Fail open on the exact original Answered: a rebuilt
                    # copy is never returned here, so a broken presenter can
                    # never corrupt or replace the sealed answer.
                    return outcome
                # Fail closed: finalize_answer_text below is the only place
                # the derived-set membership context sentence is attached,
                # and this outcome never reaches it. Returning it as-is would
                # misdescribe scope (ADR 0022, decision D1), so refuse with
                # the same reason the module already uses when set context
                # cannot be expressed (module_scoping.py, page_executor.py).
                trace.fail("module", "grain_unexpressible", grain_check_site="presenter_failed")
                return Unsupported(
                    reason_code="grain_unexpressible", message="plan cannot be executed safely"
                )
            return finalize_answer_text(
                scoped.plan,
                outcome,
                text=text,
                max_answer_chars=request.max_answer_chars,
            )
        return outcome

    async def resolve_values(
        self,
        planned: BusinessQueryPlan,
        scoped: ScopedPlan,
        request: BusinessQueryRequest,
        bundle: DefinitionBundle,
        *,
        evidence: BusinessQueryEvidenceContext | None,
        trace: QueryTrace,
        progress: BusinessProgressSink | None = None,
        ordinal: int = 0,
        of: int = 1,
        turn_budget: TurnBudget,
    ) -> (
        tuple[BusinessQueryPlan, ScopedPlan]
        | ClarificationRequired
        | Unsupported
        | Incomplete
        | Denied
    ):
        async def _commit_cb(
            *,
            resolver_query_id: str,
            request: BusinessQueryRequest,
            value_type: ResolvableType,
            evidence: BusinessQueryEvidenceContext | None,
        ) -> Incomplete | None:
            return await commit_resolver_started(
                self._evidence_ports,
                resolver_query_id=resolver_query_id,
                correlation_id=request.correlation_id,
                value_type=value_type,
                evidence=evidence,
                step_timeout_seconds=self._step_timeout_seconds,
            )

        return await resolve_values(
            planned,
            scoped,
            request,
            bundle,
            value_resolver=self._value_resolver,
            scope_fn=self._scope_fn,
            evidence=evidence,
            trace=trace,
            progress=progress,
            ordinal=ordinal,
            of=of,
            turn_budget=turn_budget,
            await_isolated_executor=self._await_isolated_executor,
            commit_resolver_started_fn=_commit_cb,
        )

    def load_bundle(
        self, principal: Principal, correlation_id: str
    ) -> DefinitionBundle | Denied | Incomplete:
        return load_bundle(self._bundle_resolver, principal, correlation_id)
