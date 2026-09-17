"""Application operation owning the Business Query request lifecycle and transaction."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.business_query.composition import build_module, business_query_evidence_config
from app.business_query.definitions import (
    BundleSelectionError,
    BundleValidationError,
    DefinitionBundle,
    InvalidBundleIndexError,
    bundle_for_manifest,
)
from app.business_query.outcomes import (
    Answered,
    BusinessQueryOutcome,
    Incomplete,
    serialize_business_query_outcome,
)
from app.business_query.plan import PlannedQuerySet
from app.business_query.plan.attempts import (
    AttemptConflictError,
    PlannerAttemptTerminal,
    PlannerTerminalClass,
    PlannerTerminalCode,
    PostgresPlannerAttemptSink,
)
from app.business_query.ports import BusinessProgressSink
from app.business_query.seal.evidence import BusinessQueryEvidenceContext
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.business_query.wire.module import BusinessQueryModule
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.request_lifecycle import BqRequestScope
from app.config import settings
from app.core.ask_errors import (
    CAPABILITY_UNAVAILABLE_MESSAGE,
    INCOMPLETE_ANSWER_MESSAGE,
)
from app.core.errors import CapabilityUnavailableError
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget, await_with_budget
from app.db.postgres import session_scope
from app.resources import ProcessResources
from app.services.business_query_mapping import (
    _RECORD_DATABASE_IDENTITY,
    denied_capability_result,
    evidence_unavailable_result,
    map_outcome,
)
from app.services.business_query_runtime import (
    evidence_ports,
)
from app.services.business_query_runtime import (
    planner_plugins as _planner_plugins,
)
from app.services.business_query_telemetry import (
    apply_query_trace,
    persist_answered_query_record,
)
from app.tools.contracts import BusinessQueryInvocation

logger = logging.getLogger(__name__)

_PLANNER_STEP_CEILING_SECONDS = 18.0


@runtime_checkable
class BqOperation(Protocol):
    """Request-scoped BQ application operation protocol."""

    async def prepare(self) -> PlannedQuerySet | AskBusinessQueryResult: ...
    async def execute(
        self, call: BusinessQueryInvocation
    ) -> CommittedBqResult | AskBusinessQueryResult: ...
    async def aclose(self) -> None: ...


def _require_v2_principal_claims(principal: Principal) -> None:
    if principal.manifest_hash is None:
        raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)
    if principal.entity_id is None and not principal.cross_entity:
        raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)


def build_bq_module(
    principal: Principal,
    *,
    resources: ProcessResources,
    correlation_id: str = "",
    attempt_session: AsyncSession | None = None,
) -> BusinessQueryModule:
    """Build the BusinessQueryModule for an Ask turn or operation."""
    _require_v2_principal_claims(principal)
    engine = resources.record_engine
    bundle = bundle_for_manifest(principal.manifest_hash or "")
    plugins = _planner_plugins(correlation_id, attempt_session=attempt_session)
    if attempt_session is not None:
        plugins = evidence_ports(plugins, attempt_session=attempt_session)
    from app.config import settings

    planner_ceiling = (
        None if settings.ask_turn_unbounded else settings.ask_planner_step_ceiling_seconds
    )
    return build_module(
        principal=principal,
        engine=engine,
        executor=resources.adapter_executor,
        bundle=bundle,
        database_identity=_RECORD_DATABASE_IDENTITY,
        planner_step_ceiling_seconds=planner_ceiling,
        plugins=plugins,
    )


_build_module = build_bq_module


async def map_and_require_query_record(
    outcome: BusinessQueryOutcome,
    *,
    shadow: bool,
    evidence_sealed: bool,
    question: str,
    principal: Principal,
    correlation_id: str,
    module: BusinessQueryModule,
) -> AskBusinessQueryResult:
    """Map outcome to AskBusinessQueryResult and persist answered Query Record."""
    mapped = map_outcome(outcome, shadow=shadow, evidence_sealed=evidence_sealed)
    mapped = apply_query_trace(mapped, module, correlation_id)
    if isinstance(outcome, Answered):
        if not business_query_evidence_config().query_record_database_url:
            return evidence_unavailable_result()
        writer = module.query_record_writer
        if not evidence_sealed or writer is None:
            return evidence_unavailable_result()
        if not await persist_answered_query_record(
            writer,
            question=question,
            principal=principal,
            correlation_id=correlation_id,
            result=mapped,
        ):
            return evidence_unavailable_result()
    return mapped


_map_and_require_query_record = map_and_require_query_record


async def abort_unanswered_uow(session: AsyncSession, mapped: AskBusinessQueryResult) -> None:
    """Roll back attempt session if mapped outcome is not answered."""
    if mapped.disposition != "answered" and hasattr(session, "rollback"):
        res = session.rollback()
        if asyncio.iscoroutine(res):
            await res


_abort_unanswered_uow = abort_unanswered_uow


def _answer_query_ids(outcome: Answered) -> tuple[str, ...]:
    """Pure helper: receipt AQID of Answered followed by companion_answered receipt AQIDs."""
    aqids: list[str] = []
    if getattr(outcome, "receipt", None) and outcome.receipt.answer_query_id:
        aqids.append(outcome.receipt.answer_query_id)
    for comp in getattr(outcome, "companion_answered", ()):
        if getattr(comp, "receipt", None) and comp.receipt.answer_query_id:
            aqids.append(comp.receipt.answer_query_id)
    return tuple(aqids)


class PreparedBqOperation:
    """Application owner of the BQ preparation, execution, and transaction lifecycle."""

    def __init__(  # noqa: PLR0913
        self,
        request: BusinessQueryRequest,
        *,
        resources: ProcessResources,
        progress: BusinessProgressSink | None = None,
        evidence: BusinessQueryEvidenceContext | None = None,
        turn_budget: TurnBudget = UNBOUNDED_BUDGET,
        create_module: Callable[[AsyncSession | None], BusinessQueryModule] | None = None,
    ) -> None:
        self._request = request
        self._resources = resources
        self._progress = progress
        self._evidence = evidence
        self._turn_budget = turn_budget
        self._create_module = create_module or self._default_module
        self._stack: AsyncExitStack | None = None
        self._session: AsyncSession | None = None
        self._module: BusinessQueryModule | None = None
        self._scope: BqRequestScope | None = None
        self._prepared_set: PlannedQuerySet | None = None
        self._normalized_request: BusinessQueryRequest | None = None
        self._bundle: DefinitionBundle | None = None
        self._committed: bool = False
        self._closed: bool = False
        self._shadow: bool = settings.business_query_mode == "shadow"
        self._attempt_sink = None
        self._attempt_id: str | None = None
        self._attempt_lease_epoch: int = 1

    @property
    def request(self) -> BusinessQueryRequest:
        return self._request

    @property
    def turn_budget(self) -> TurnBudget:
        return self._turn_budget

    def _bind_attempt_journal(self) -> None:
        planner = getattr(self._module, "planner", None) or getattr(self._module, "_planner", None)
        self._attempt_sink = getattr(planner, "_attempt_sink", None)
        self._attempt_id = getattr(planner, "_consumed_attempt_id", None)
        ctx = getattr(planner, "_attempt_context", None)
        epoch = getattr(ctx, "lease_epoch", None)
        self._attempt_lease_epoch = int(epoch) if epoch is not None else 1

    async def _record_uncertain_completion(self) -> None:
        sink = self._attempt_sink
        attempt_id = self._attempt_id
        if sink is None or not attempt_id:
            return
        terminal = PlannerAttemptTerminal(
            terminal_class=PlannerTerminalClass.COMPLETION_UNKNOWN,
            terminal_code=PlannerTerminalCode.COMPLETION_UNKNOWN,
            duration_ms=0,
            finished_at=datetime.now(UTC),
        )
        try:
            if isinstance(sink, PostgresPlannerAttemptSink):
                async with session_scope(resources=self._resources) as recovery:
                    bound = PostgresPlannerAttemptSink(recovery)
                    await bound.finish(
                        attempt_id,
                        expected_lease_epoch=self._attempt_lease_epoch,
                        terminal=terminal,
                    )
            else:
                await sink.finish(
                    attempt_id,
                    expected_lease_epoch=self._attempt_lease_epoch,
                    terminal=terminal,
                )
        except AttemptConflictError:
            logger.warning(
                "uncertain commit journal refused correlation_id=%s",
                self._request.correlation_id,
            )
        except Exception:
            logger.warning(
                "uncertain commit journal failed correlation_id=%s",
                self._request.correlation_id,
            )

    def _default_module(self, session: AsyncSession | None) -> BusinessQueryModule:
        return build_bq_module(
            self._request.principal,
            resources=self._resources,
            correlation_id=self._request.correlation_id,
            attempt_session=session,
        )

    async def prepare(self) -> PlannedQuerySet | AskBusinessQueryResult:
        if self._closed:
            raise RuntimeError("PreparedBqOperation is already closed")
        if settings.business_query_mode == "disabled":
            raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)

        self._turn_budget.check_not_expired()
        self._stack = AsyncExitStack()
        try:
            # Without a Query Record store there is no attempt journal or
            # answered-record transaction; the module still plans and executes.
            if business_query_evidence_config().query_record_database_url:
                self._session = await self._stack.enter_async_context(
                    session_scope(resources=self._resources)
                )
            self._module = self._create_module(self._session)
            self._bind_attempt_journal()

            self._scope = await self._stack.enter_async_context(
                self._module.request_scope(self._request, turn_budget=self._turn_budget)
            )

            planned_or_outcome = await self._module.plan_round(
                self._scope.request,
                progress=self._progress,
                trace=self._scope.trace,
                turn_budget=self._turn_budget,
            )

            if not isinstance(planned_or_outcome, tuple):
                finished = self._scope.finish(planned_or_outcome)
                mapped = await map_and_require_query_record(
                    finished,
                    shadow=self._shadow,
                    evidence_sealed=False,
                    question=self._request.question,
                    principal=self._request.principal,
                    correlation_id=self._request.correlation_id,
                    module=self._module,
                )
                if self._session is not None and hasattr(self._session, "rollback"):
                    res = self._session.rollback()
                    if asyncio.iscoroutine(res):
                        await res
                await self._stack.aclose()
                self._closed = True
                return mapped

            self._normalized_request, self._prepared_set, self._bundle = planned_or_outcome
            return self._prepared_set
        except CapabilityUnavailableError:
            await self.aclose()
            return denied_capability_result()
        except BundleSelectionError:
            await self.aclose()
            return denied_capability_result()
        except AttemptConflictError:
            await self.aclose()
            raise
        except (
            InvalidBundleIndexError,
            BundleValidationError,
            OSError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:
            logger.warning(
                "business query composition failed correlation_id=%s error=%s",
                self._request.correlation_id,
                type(exc).__name__,
            )
            await self.aclose()
            return AskBusinessQueryResult(
                disposition="incomplete",
                answer_text=INCOMPLETE_ANSWER_MESSAGE,
                completion_status="incomplete",
                sql_stop_reason="adapter_invalid",
                model=None,
                input_tokens=None,
                output_tokens=None,
                raise_capability_unavailable=False,
                business_query=serialize_business_query_outcome(
                    Incomplete(reason_code="adapter_invalid")
                ),
            )
        except BaseException:
            await self.aclose()
            raise

    async def execute(
        self, call: BusinessQueryInvocation
    ) -> CommittedBqResult | AskBusinessQueryResult:
        if self._closed:
            raise RuntimeError("PreparedBqOperation is already closed")
        if (
            self._prepared_set is None
            or self._module is None
            or self._scope is None
            or self._normalized_request is None
            or self._stack is None
        ):
            raise RuntimeError("PreparedBqOperation has not been prepared")

        self._turn_budget.check_not_expired()
        try:
            invocation_plans = call.arguments.to_planned_query_set()
            if invocation_plans != self._prepared_set:
                outcome: BusinessQueryOutcome = Incomplete(reason_code="adapter_invalid")
                outcome = self._scope.finish(outcome)
                mapped = map_outcome(outcome, shadow=self._shadow, evidence_sealed=False)
                if self._session is not None and hasattr(self._session, "rollback"):
                    res = self._session.rollback()
                    if asyncio.iscoroutine(res):
                        await res
                await self._stack.aclose()
                self._closed = True
                return mapped

            outcome = await self._module.execute_planned_set(
                self._normalized_request,
                self._prepared_set,
                progress=self._progress,
                evidence=self._evidence,
                trace=self._scope.trace,
                turn_budget=self._turn_budget,
            )

            outcome = self._scope.finish(outcome)
            mapped = await map_and_require_query_record(
                outcome,
                shadow=self._shadow,
                evidence_sealed=self._evidence is not None,
                question=self._request.question,
                principal=self._request.principal,
                correlation_id=self._request.correlation_id,
                module=self._module,
            )

            if mapped.disposition != "answered":
                if self._session is not None and hasattr(self._session, "rollback"):
                    res = self._session.rollback()
                    if asyncio.iscoroutine(res):
                        await res
                await self._stack.aclose()
                self._closed = True
                return mapped

            self._turn_budget.check_not_expired()
            try:
                await await_with_budget(self._stack.aclose, self._turn_budget)
            except BaseException:
                self._closed = True
                await self._record_uncertain_completion()
                raise
            self._committed = True
            self._closed = True
            return CommittedBqResult(result=mapped, answer_queries=_answer_query_ids(outcome))
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if (
                not self._committed
                and self._session is not None
                and hasattr(self._session, "rollback")
            ):
                res = self._session.rollback()
                if asyncio.iscoroutine(res):
                    await res
        finally:
            if self._stack is not None:
                await self._stack.aclose()
