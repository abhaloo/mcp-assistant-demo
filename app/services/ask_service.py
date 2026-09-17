"""
Ask orchestration: classify → dispatch → merge.

HTTP concerns live in app/api/router.py; this module owns the Q&A business flow.
"""

from __future__ import annotations

import asyncio
import uuid

from langsmith import traceable
from opentelemetry import trace

from app.auth import Principal
from app.config import settings  # noqa: F401  -- conversation_enabled patch anchor for tests
from app.conversation.turn import TurnContext
from app.core.ask_errors import (
    CAPABILITY_UNAVAILABLE_MESSAGE,
)
from app.core.errors import CapabilityUnavailableError
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget, await_with_budget
from app.models.schemas import Answer, Question
from app.models.tool_results import TurnResult, tool_result_fields
from app.query_records.wiring import monotonic_start, resolve_prompt_version
from app.resources import ProcessResources
from app.services.answer_finalize import bq_digest_for_persist, persist_and_enrich
from app.services.ask import ProgressSink, _still_connected
from app.services.ask import ask as interpret_ask
from app.services.ask_answer_builder import make_answer
from app.services.ask_observation import (
    note_query_record_terminal,
    note_request_outcome,
    with_root_tracing_context,
)
from app.services.ask_outcome import (  # noqa: F401
    Answered,
    BranchResult,
    CapabilityUnavailable,
    FixedMessage,
    GuardError,
    GuardOutcome,
    Replayed,
    ResultPage,
    Stopped,
    TurnEvidence,
    guarded_call,
    sources_from_docs,
    to_json_source,
)
from app.services.ask_prepare import (
    DispatchClassified,
    PreparedAsk,
    PreparedCoordinatorTurn,
    prepare_ask,
    reference_artifact_for_turn,
)
from app.services.ask_result_projection import (
    make_on_classified as _make_on_classified,
)
from app.services.ask_result_projection import (
    stamp_classify_prompt_version as _stamp_classify_prompt_version,
)
from app.services.ask_v2_turn_bound import new_expiry_event
from app.services.business_query_service import AskBusinessQueryResult
from app.services.cancel_service import begin_finalize, mark_done, register_run
from app.services.continuation_tokens import complete_continuation_token
from app.services.record_intent import RecordAggregateContinuation
from app.services.run_lifecycle import (
    RunOutcome,
    finalize_trace,
    operational_trace_inputs,
    operational_trace_outputs,
)
from app.telemetry.correlation import bind_correlation_id, bind_thread_id, clear_correlation_id
from app.telemetry.invocation_payload import (
    ExecutionIdentity,
    InvocationExpectation,
    require_terminal_evidence,
)


def _with_turn_result(answer: Answer, turn_result: TurnResult | None) -> Answer:
    """Project a version 1 result onto the answer and keep it for the SSE transports."""
    if turn_result is None:
        return answer
    return answer.model_copy(update={**tool_result_fields(turn_result), "turn_result": turn_result})


async def _finalize_answer(
    *,
    body: Question,
    principal: Principal,
    ctx: TurnContext,
    answer: Answer,
    lifecycle_run_id: str | None,
    model_invoked: bool,
    follow_up_suggestions: list[str] | None = None,
    aggregate_continuation: RecordAggregateContinuation | None = None,
    continuation_request_id: str | None = None,
    bq: AskBusinessQueryResult | None = None,
    evidence: TurnEvidence | None = None,
) -> Answer:
    """Atomically persist an answer while optional response enrichment runs.

    ``model_invoked`` reflects the route decision made before this answer
    existed: the caller already knows, from the outcome variant `interpret_ask`
    returned, whether the turn dispatched to the semantic RAG generation seam.
    Reading it off the produced answer's fields (model sentinel,
    sql_stop_reason, business_query outcome, ...) let an outcome that
    self-reports as "answered" decide whether its own evidence gate applies.
    """
    trace.get_current_span().set_attribute("effective_route", answer.query_type)
    if lifecycle_run_id is not None and not await begin_finalize(lifecycle_run_id):
        raise asyncio.CancelledError

    correlation_id = lifecycle_run_id or body.run_id
    if correlation_id:
        await require_terminal_evidence(
            ExecutionIdentity(correlation_id=correlation_id),
            InvocationExpectation(min_invocations=1 if model_invoked else 0),
        )

    result = await persist_and_enrich(
        ctx=ctx,
        principal=principal,
        question=body.question,
        answer=answer.answer,
        follow_up_suggestions=follow_up_suggestions,
        reference_artifact=reference_artifact_for_turn(
            body, ctx, answer_query_type=answer.query_type
        ),
        aggregate_continuation=aggregate_continuation,
        bq_digest=bq_digest_for_persist(query_type=answer.query_type, bq=bq),
        run_id=lifecycle_run_id or body.run_id,
        evidence=evidence,
        answer_mode=answer.answer_mode,
        source_exchange_ids=answer.source_exchange_ids,
    )
    answer.follow_up_suggestions = result.follow_up_suggestions
    if result.restore_ref is not None:
        answer.restore_ref = result.restore_ref
    if result.persisted.thread_id is not None:
        answer.thread_id = result.persisted.thread_id
    if result.persisted.exchange_id is not None:
        answer.exchange_id = result.persisted.exchange_id
        if result.trace_id is not None:
            answer.trace_id = result.trace_id
            answer.feedback_token = result.feedback_token
    if body.continuation_token is not None:
        await complete_continuation_token(
            body.continuation_token,
            principal=principal,
            thread_id=ctx.thread_id,
            question=body.question,
            request_id=continuation_request_id or body.idempotency_key or body.run_id or "implicit",
            answer=answer.model_dump(mode="json"),
        )
    if lifecycle_run_id is not None:
        await mark_done(lifecycle_run_id)
    return answer


class AskService:
    """Orchestrates semantic, structured, and hybrid Q&A paths."""

    async def ask(
        self,
        body: Question,
        principal: Principal,
        *,
        resources: ProcessResources,
        progress: ProgressSink | None = None,
        turn_budget: TurnBudget = UNBOUNDED_BUDGET,
        expiry_event: asyncio.Event | None = None,
    ) -> Answer:
        """Thin wrapper: register the run, then let the seam wrapper gate the
        root LangSmith tracing context around one `_ask_traced` attempt --
        see app.services.ask_observation.with_root_tracing_context."""
        run_id = body.run_id or uuid.uuid4().hex
        bind_correlation_id(run_id)
        bind_thread_id(body.thread_id)
        task = asyncio.current_task()
        assert task is not None
        signal = expiry_event if expiry_event is not None else new_expiry_event()
        try:
            async with register_run(run_id, principal, task):
                return await await_with_budget(
                    lambda: with_root_tracing_context(
                        lambda: self._ask_traced(
                            body,
                            principal,
                            resources=resources,
                            lifecycle_run_id=run_id,
                            progress=progress,
                            turn_budget=turn_budget,
                            expiry_event=signal,
                        )
                    ),
                    turn_budget,
                    on_budget_expired=signal.set,
                )
        finally:
            clear_correlation_id()

    @traceable(
        name="ask",
        run_type="chain",
        process_inputs=operational_trace_inputs,
        process_outputs=operational_trace_outputs,
    )
    async def _ask_traced(
        self,
        body: Question,
        principal: Principal,
        *,
        resources: ProcessResources,
        lifecycle_run_id: str | None = None,
        progress: ProgressSink | None = None,
        run_tree=None,
        turn_budget: TurnBudget = UNBOUNDED_BUDGET,
        expiry_event: asyncio.Event | None = None,
    ) -> Answer:
        """One root per JSON attempt with an explicit terminal outcome."""
        started_at = monotonic_start()
        outcome: RunOutcome = "error"
        query_type: str | None = None
        answer: Answer | None = None
        bq_result: AskBusinessQueryResult | None = None
        ctx: TurnContext | None = None
        try:
            # Stamped before prepare_ask so classify's child span (which
            # re-stamps it via on_classified) can never precede the root.
            if lifecycle_run_id is not None:
                trace.get_current_span().set_attribute("correlation_id", lifecycle_run_id)
            # prepare_ask() resolves the turn -- transcript load + condense.
            # Run it ONCE here and hand the result down: resolving again
            # inside _ask_impl cost a second condense LLM call and a second
            # store load on every follow-up turn.
            if body.result_page_cursor is not None:
                page = await interpret_ask(
                    body,
                    principal,
                    resources=resources,
                    progress=progress,
                    disconnected=_still_connected,
                    lifecycle_run_id=lifecycle_run_id,
                    turn_budget=turn_budget,
                )
                if not isinstance(page, ResultPage):
                    raise AssertionError(f"cursor ask must return ResultPage, got {type(page)!r}")
                query_type = "structured"
                answer, bq_result = page.answer, page.bq
                ctx = page.ctx
            else:
                prepared = await prepare_ask(
                    body,
                    principal,
                    resources=resources,
                    on_classified=(
                        _make_on_classified(lifecycle_run_id)
                        if lifecycle_run_id is not None
                        else _stamp_classify_prompt_version
                    ),
                    turn_budget=turn_budget,
                )
                ctx = prepared.turn.ctx
                answer, bq_result = await self._ask_impl(
                    body,
                    principal,
                    prepared,
                    resources=resources,
                    lifecycle_run_id=lifecycle_run_id,
                    progress=progress,
                    turn_budget=turn_budget,
                )
            if bq_result is not None and bq_result.raise_capability_unavailable:
                raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)
            outcome = "completed"
            query_type = answer.query_type
        except asyncio.CancelledError:
            timed_out = expiry_event is not None and expiry_event.is_set()
            finalize_trace(run_tree, "error" if timed_out else "stopped")
            outcome = "error" if timed_out else "stopped"
            raise
        except BaseException:
            finalize_trace(run_tree, "error")
            outcome = "error"
            raise
        else:
            finalize_trace(
                run_tree,
                "completed",
                query_type=answer.query_type,
                committed=answer.exchange_id is not None,
            )
            return answer
        finally:
            budget_timeout = (
                outcome == "error" and expiry_event is not None and expiry_event.is_set()
            )
            note_query_record_terminal(
                body=(
                    body
                    if body.question is not None
                    else body.model_copy(update={"question": "[business query result page]"})
                ),
                principal=principal,
                ctx=ctx,
                run_outcome=outcome,
                query_type=query_type,
                started_at=started_at,
                prompt_version=resolve_prompt_version(query_type),
                correlation_id=lifecycle_run_id or body.run_id,
                model=bq_result.model if bq_result is not None else None,
                input_tokens=bq_result.input_tokens if bq_result is not None else None,
                output_tokens=bq_result.output_tokens if bq_result is not None else None,
                reasoning_tokens=bq_result.reasoning_tokens if bq_result is not None else None,
                resolver_disposition=bq_result.disposition if bq_result is not None else None,
                bq_trace_json=bq_result.bq_trace_json if bq_result is not None else None,
                stable_error_code="timeout" if budget_timeout else None,
                timeout=True if budget_timeout else None,
                cancelled=True if outcome == "stopped" else None,
            )

    async def _ask_impl(
        self,
        body: Question,
        principal: Principal,
        prepared: PreparedAsk,
        *,
        resources: ProcessResources,
        lifecycle_run_id: str | None = None,
        progress: ProgressSink | None = None,
        turn_budget: TurnBudget,
    ) -> tuple[Answer, AskBusinessQueryResult | None]:
        outcome = await interpret_ask(
            body,
            principal,
            resources=resources,
            progress=progress,
            disconnected=_still_connected,
            lifecycle_run_id=lifecycle_run_id,
            prepared=prepared,
            turn_budget=turn_budget,
        )
        match outcome:
            case Stopped():
                raise asyncio.CancelledError
            case Replayed(answer=answer):
                return answer, None
            case Answered() as answered:
                trace.get_current_span().set_attribute("requested_route", answered.query_type)
                follow_ups = (
                    None
                    if answered.follow_up_suggestions is None
                    else list(answered.follow_up_suggestions)
                )
                answer = make_answer(
                    body,
                    answer=answered.answer_text,
                    sources=[to_json_source(src) for src in answered.sources],
                    query_type=answered.query_type,
                    sql_provenance=answered.sql_provenance,
                    follow_up_suggestions=follow_ups or [],
                    citations=answered.citations,
                    completion_status=answered.completion_status,
                    sql_stop_reason=answered.sql_stop_reason,
                    stage_accumulator=answered.stage_models,
                    final_producer_purpose=answered.final_producer_purpose,
                    fulfillment_scope=answered.fulfillment_scope,
                    omitted_capabilities=list(answered.omitted_capabilities),
                    banner=answered.banner,
                    business_query=answered.business_query,
                    presentation=answered.presentation,
                    follow_up_offer=answered.follow_up_offer,
                    answer_mode=answered.answer_mode,
                    source_exchange_ids=answered.source_exchange_ids,
                )
                answer = _with_turn_result(answer, answered.turn_result)
                if answered.completion_status == "incomplete":
                    note_request_outcome(query_type=answered.query_type, outcome="incomplete")
                else:
                    note_request_outcome(query_type=answered.query_type, outcome="ok")
                assert answered.ctx is not None
                finalized = await _finalize_answer(
                    body=body,
                    principal=principal,
                    ctx=answered.ctx,
                    answer=answer,
                    lifecycle_run_id=lifecycle_run_id,
                    model_invoked=isinstance(prepared, PreparedCoordinatorTurn)
                    or answered.query_type == "semantic",
                    follow_up_suggestions=follow_ups,
                    continuation_request_id=answered.continuation_request_id,
                    bq=answered.bq,
                    evidence=TurnEvidence.from_answered(
                        answered, response_policy=body.response_policy
                    ),
                )
                return finalized, answered.bq
            case CapabilityUnavailable() as unavailable:
                query_type = (
                    prepared.query_type
                    if isinstance(prepared, DispatchClassified)
                    else "structured"
                )
                answer = make_answer(
                    body,
                    answer="",
                    sources=[],
                    query_type=query_type,
                    stage_accumulator=prepared.turn.stage_models,
                    fixed_response=True,
                    business_query=(
                        unavailable.bq.business_query if unavailable.bq is not None else None
                    ),
                )
                answer = _with_turn_result(answer, unavailable.turn_result)
                return answer, unavailable.bq
            case FixedMessage() as fixed:
                message = fixed.message
                query_type = fixed.query_type
                stage_models = fixed.stage_models
                ctx = fixed.ctx
                continuation_token = fixed.continuation_token
                omitted_capabilities = fixed.omitted_capabilities
                banner = fixed.banner
                answer = make_answer(
                    body,
                    answer=message,
                    sources=[],
                    query_type=query_type,
                    stage_accumulator=stage_models,
                    fixed_response=True,
                    continuation_token=continuation_token,
                    omitted_capabilities=list(omitted_capabilities),
                    banner=banner,
                )
                answer = _with_turn_result(answer, fixed.turn_result)
                note_request_outcome(query_type=query_type, outcome="ok")
                finalized = await _finalize_answer(
                    body=body,
                    principal=principal,
                    ctx=ctx,
                    answer=answer,
                    lifecycle_run_id=lifecycle_run_id,
                    model_invoked=False,
                    continuation_request_id=None,
                )
                return finalized, None
            case _:
                raise AssertionError(f"unhandled ask outcome: {type(outcome)!r}")
