"""SSE streaming orchestration for POST /api/ask."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from langsmith import traceable
from opentelemetry import trace

from app.auth import Principal
from app.config import settings
from app.conversation.turn import TurnContext
from app.core.ask_errors import (
    report_ask_failure,
)
from app.core.errors import (
    CapabilityUnavailableError,
    ModelRouteUnavailableError,
)
from app.models.schemas import QueryType, Question, SqlProvenance
from app.providers.model_purpose import ModelPurpose
from app.providers.model_registry import PolicyViolationError
from app.providers.route_policy import RouteResolutionError
from app.providers.stage_model_report import (
    StageModelAccumulator,
)
from app.query_records.bq_usage import fill_terminal_usage_from_bq
from app.query_records.context import TerminalUsageCapture
from app.query_records.wiring import monotonic_start, resolve_prompt_version
from app.rag.page_context import UnknownPageContextProfileError
from app.resources import ProcessResources
from app.services.ask import ask as interpret_ask
from app.services.ask_deadline import clamp_turn_budget
from app.services.ask_frames import (
    HEARTBEAT_INTERVAL_SECONDS,
    AskFrame,
    DataFrame,
    Disconnected,
    HeartbeatFrame,
    StatusFrame,
)
from app.services.ask_observation import (
    note_query_record_terminal,
    note_request_outcome,
    with_root_tracing_context,
)
from app.services.ask_outcome import (
    Answered,
    CapabilityUnavailable,
    FixedMessage,
    Replayed,
    ResultPage,
    Stopped,
    to_sse_source,
)
from app.services.ask_prepare import (
    prepare_ask,
)
from app.services.ask_progress import AskProgressSink
from app.services.business_query_stream import produce_bq_stream, produce_result_page_stream
from app.services.cancel_service import (
    register_run,
)
from app.services.record_intent import RecordAggregateContinuation
from app.services.run_lifecycle import (
    RunOutcome,
    StreamTerminal,
    finalize_trace,
    operational_trace_inputs,
    operational_trace_outputs,
)
from app.services.stream_transport import (
    chunk_answer_tokens as _chunk_answer_tokens,
)
from app.services.stream_transport import (
    client_gone as _client_gone,
)
from app.services.stream_transport import (
    commit_done as _commit_done,
)
from app.services.stream_transport import (
    sse_done_payload as _sse_done_payload,
)
from app.services.stream_transport import (
    sse_producer_errors as _sse_producer_errors,
)
from app.services.stream_transport import sse_sources_from_answer_sources
from app.telemetry.correlation import (
    bind_correlation_id,
    bind_thread_id,
    clear_correlation_id,
    clear_thread_id,
)

logger = logging.getLogger(__name__)


async def _produce_records_only_stream(
    body: Question,
    principal: Principal,
    ctx: TurnContext,
    disconnected: Disconnected,
    queue: asyncio.Queue[AskFrame | None],
    terminal: StreamTerminal,
    run_id: str,
    *,
    answered: Answered | None = None,
    stage_accumulator: StageModelAccumulator | None = None,
) -> RunOutcome:
    """SSE records-only producer: tokens → empty sources → sql_provenance links → done."""
    async with _sse_producer_errors(terminal, query_type="structured", role=principal.role) as box:
        if await _client_gone(disconnected, run_id):
            note_request_outcome(query_type="structured", outcome="stopped")
            return "stopped"
        if answered is None:
            note_request_outcome(query_type="structured", outcome="stopped")
            return "stopped"
        await queue.put(StatusFrame("writing_answer"))
        # Chunk so clients can pace; still one logical answer.
        text = answered.answer_text
        chunk_size = 48
        for i in range(0, len(text), chunk_size):
            if await _client_gone(disconnected, run_id):
                note_request_outcome(query_type="structured", outcome="stopped")
                return "stopped"
            await queue.put(DataFrame("token", {"d": text[i : i + chunk_size]}))
        await queue.put(DataFrame("sources", []))
        sql_prov = answered.sql_provenance or SqlProvenance(queries=[], record_links=[])
        await queue.put(DataFrame("sql_provenance", sql_prov.model_dump()))

        outcome = await _commit_done(
            body=body,
            principal=principal,
            ctx=ctx,
            disconnected=disconnected,
            terminal=terminal,
            answer=answered.answer_text,
            done_payload=_sse_done_payload(
                stage_accumulator,
                final_producer_purpose=ModelPurpose.record_reasoning,
            ),
            run_id=run_id,
            model_invoked=False,
            follow_up_suggestions=list(answered.follow_up_suggestions or ()),
        )
        note_request_outcome(
            query_type="structured",
            outcome="ok" if outcome == "completed" else "stopped",
        )
        box.value = outcome
    return box.value


async def _produce_rehydration_message_stream(
    body: Question,
    principal: Principal,
    ctx: TurnContext,
    disconnected: Disconnected,
    queue: asyncio.Queue[AskFrame | None],
    terminal: StreamTerminal,
    run_id: str,
    message: str,
    aggregate_continuation: RecordAggregateContinuation | None = None,
    metric_query_type: QueryType = "semantic",
    *,
    stage_accumulator: StageModelAccumulator | None = None,
    extra: dict | None = None,
) -> RunOutcome:
    """SSE counterpart of the JSON path's outcome->message short-circuit
    (task A7c, req 5): a stale/infra-broken ledger streams a fixed message,
    exactly like _produce_records_only_stream's shape (tokens -> empty
    sources -> done), never the normal semantic/structured dispatch.
    """
    async with _sse_producer_errors(
        terminal, query_type=metric_query_type, role=principal.role
    ) as box:
        if await _client_gone(disconnected, run_id):
            return "stopped"
        await queue.put(StatusFrame("writing_answer"))
        for piece in _chunk_answer_tokens(message):
            if await _client_gone(disconnected, run_id):
                return "stopped"
            await queue.put(DataFrame("token", {"d": piece}))
        await queue.put(DataFrame("sources", []))

        outcome = await _commit_done(
            body=body,
            principal=principal,
            ctx=ctx,
            disconnected=disconnected,
            terminal=terminal,
            answer=message,
            done_payload=_sse_done_payload(stage_accumulator, fixed_response=True, extra=extra),
            run_id=run_id,
            model_invoked=False,
            aggregate_continuation=aggregate_continuation,
        )
        note_request_outcome(
            query_type=metric_query_type,
            outcome="ok" if outcome == "completed" else "stopped",
        )
        box.value = outcome
    return box.value


async def _produce_semantic_stream(
    body: Question,
    principal: Principal,
    ctx: TurnContext,
    disconnected: Disconnected,
    queue: asyncio.Queue[AskFrame | None],
    terminal: StreamTerminal,
    run_id: str,
    *,
    answered: Answered,
    usage: TerminalUsageCapture | None = None,
    stage_accumulator: StageModelAccumulator | None = None,
) -> RunOutcome:
    """SSE emit adapter for a semantic Answered outcome."""
    async with _sse_producer_errors(terminal, query_type="semantic", role=principal.role) as box:
        if await _client_gone(disconnected, run_id):
            note_request_outcome(query_type="semantic", outcome="stopped")
            return "stopped"
        input_tokens = answered.usage.input_tokens if answered.usage is not None else None
        output_tokens = answered.usage.output_tokens if answered.usage is not None else None
        cost_status = answered.usage.cost_status if answered.usage is not None else None
        if usage is not None and answered.usage is not None:
            usage.input_tokens = answered.usage.input_tokens
            usage.output_tokens = answered.usage.output_tokens
            usage.reasoning_tokens = answered.usage.reasoning_tokens
            usage.cost_status = answered.usage.cost_status
        source_payload = [to_sse_source(src) for src in answered.sources]
        for piece in _chunk_answer_tokens(answered.answer_text):
            if await _client_gone(disconnected, run_id):
                return "stopped"
            await queue.put(DataFrame("token", {"d": piece}))
        await queue.put(DataFrame("sources", source_payload))
        await queue.put(DataFrame("citations", answered.citations.model_dump()))
        outcome = await _commit_done(
            body=body,
            principal=principal,
            ctx=ctx,
            disconnected=disconnected,
            terminal=terminal,
            answer=answered.answer_text,
            done_payload=_sse_done_payload(
                stage_accumulator,
                final_producer_purpose=ModelPurpose.rag_answer,
                extra={
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                    "cost_status": cost_status,
                },
            ),
            run_id=run_id,
            model_invoked=True,
        )
        note_request_outcome(
            query_type="semantic",
            outcome="ok" if outcome == "completed" else "stopped",
        )
        box.value = outcome
    return box.value


@traceable(
    name="ask_stream",
    run_type="chain",
    process_inputs=operational_trace_inputs,
    process_outputs=operational_trace_outputs,
)
async def _run_sse_attempt(
    body: Question,
    principal: Principal,
    disconnected: Disconnected,
    queue: asyncio.Queue[AskFrame | None],
    terminal: StreamTerminal,
    *,
    resources: ProcessResources,
    run_tree=None,
) -> dict:
    """Own one SSE attempt from registration through one terminal outcome."""
    run_id = body.run_id or uuid.uuid4().hex
    bind_correlation_id(run_id)
    bind_thread_id(body.thread_id)
    query_type = "semantic"
    outcome: RunOutcome = "error"
    committed = False
    started_at = monotonic_start()
    ctx: TurnContext | None = None
    stage_models: StageModelAccumulator | None = None
    usage_capture = TerminalUsageCapture()
    turn_budget = clamp_turn_budget()
    task = asyncio.current_task()
    assert task is not None
    try:
        async with _sse_producer_errors(
            terminal,
            query_type=None,
            role=principal.role,
            stage="prepare",
            pass_through=(
                UnknownPageContextProfileError,
                RouteResolutionError,
                PolicyViolationError,
            ),
        ) as box:
            async with register_run(run_id, principal, task):
                await queue.put(StatusFrame("connecting"))
                if await _client_gone(disconnected, run_id):
                    outcome = "stopped"
                    return {"outcome": outcome, "query_type": query_type, "committed": False}

                # A result-page cursor is a stored-plan operation, not a new
                # natural-language turn.  Keep it ahead of prepare_ask so the
                # classifier/planner cannot be invoked for cursor-only requests.
                if body.result_page_cursor is not None:
                    query_type = "structured"
                    await queue.put(StatusFrame("reading_next_page"))
                    page_outcome = await interpret_ask(
                        body,
                        principal,
                        resources=resources,
                        progress=AskProgressSink(queue),
                        disconnected=disconnected,
                        lifecycle_run_id=run_id,
                        turn_budget=turn_budget,
                    )
                    if not isinstance(page_outcome, ResultPage):
                        raise AssertionError(
                            f"cursor ask must return ResultPage, got {type(page_outcome)!r}"
                        )
                    ctx = page_outcome.ctx or TurnContext(
                        thread_id=body.thread_id,
                        history=[],
                        search_query="[business query result page]",
                        original_question=body.question or "[business query result page]",
                    )
                    trace.get_current_span().set_attribute("requested_route", "structured")
                    trace.get_current_span().set_attribute("effective_route", "structured")
                    outcome = await produce_result_page_stream(
                        body=body,
                        principal=principal,
                        resources=resources,
                        ctx=ctx,
                        disconnected=disconnected,
                        queue=queue,
                        terminal=terminal,
                        run_id=run_id,
                        page=page_outcome,
                        turn_budget=turn_budget,
                    )
                    committed = outcome == "completed" and terminal.terminal == "done"
                    return {"outcome": outcome, "query_type": query_type, "committed": committed}

                # prepare_ask() owns the whole pre-dispatch decision (see its docstring).
                # This transport owns only what follows it: status events, producer
                # dispatch, and SSE finalization.
                # `query_type` here is "structured" for records_only -- a dispatch mode,
                # not a classifier result -- and prepared.query_type otherwise.
                # on_classified re-stamps correlation_id onto the classify span, which is a
                # separate child span and does not inherit the caller's attributes.
                def _on_classified(span, _query_type) -> None:
                    span.set_attribute("correlation_id", run_id)

                prepared = await prepare_ask(
                    body,
                    principal,
                    resources=resources,
                    on_classified=_on_classified,
                    turn_budget=turn_budget,
                )
                ctx = prepared.turn.ctx
                stage_models = prepared.turn.stage_models
                if await _client_gone(disconnected, run_id):
                    outcome = "stopped"
                    return {"outcome": outcome, "query_type": query_type, "committed": False}
                ladder_outcome = await interpret_ask(
                    body,
                    principal,
                    resources=resources,
                    progress=AskProgressSink(queue),
                    disconnected=disconnected,
                    lifecycle_run_id=run_id,
                    on_classified=_on_classified,
                    prepared=prepared,
                    turn_budget=turn_budget,
                )
                if isinstance(ladder_outcome, Stopped):
                    query_type = ladder_outcome.query_type
                    if usage_capture is not None and ladder_outcome.usage is not None:
                        usage_capture.input_tokens = ladder_outcome.usage.input_tokens
                        usage_capture.output_tokens = ladder_outcome.usage.output_tokens
                        usage_capture.reasoning_tokens = ladder_outcome.usage.reasoning_tokens
                        usage_capture.cost_status = ladder_outcome.usage.cost_status
                    note_request_outcome(query_type=query_type, outcome="stopped")
                    outcome = "stopped"
                    return {"outcome": outcome, "query_type": query_type, "committed": False}
                if isinstance(ladder_outcome, Replayed):
                    replayed = ladder_outcome.answer
                    query_type = ladder_outcome.query_type
                    trace.get_current_span().set_attribute("requested_route", query_type)
                    effective_route = query_type
                    await queue.put(StatusFrame("writing_answer"))
                    for piece in _chunk_answer_tokens(replayed.answer):
                        if await _client_gone(disconnected, run_id):
                            note_request_outcome(query_type=query_type, outcome="stopped")
                            outcome = "stopped"
                            return {
                                "outcome": outcome,
                                "query_type": query_type,
                                "committed": False,
                            }
                        await queue.put(DataFrame("token", {"d": piece}))
                    await queue.put(
                        DataFrame("sources", sse_sources_from_answer_sources(replayed.sources))
                    )
                    done_payload = _sse_done_payload(stage_models, fixed_response=True)
                    outcome = await _commit_done(
                        body=body,
                        principal=principal,
                        ctx=ctx,
                        disconnected=disconnected,
                        terminal=terminal,
                        answer=replayed.answer,
                        done_payload=done_payload,
                        run_id=run_id,
                        model_invoked=False,
                    )
                    trace.get_current_span().set_attribute("effective_route", effective_route)
                    committed = outcome == "completed" and terminal.terminal == "done"
                    note_request_outcome(
                        query_type=query_type,
                        outcome="ok" if outcome == "completed" else "stopped",
                    )
                    return {"outcome": outcome, "query_type": query_type, "committed": committed}
                if isinstance(ladder_outcome, FixedMessage):
                    query_type = ladder_outcome.query_type
                    trace.get_current_span().set_attribute("requested_route", query_type)
                    extra = {}
                    if ladder_outcome.continuation_token is not None:
                        extra["continuation_token"] = ladder_outcome.continuation_token
                        if ladder_outcome.omitted_capabilities:
                            extra["omitted_capabilities"] = list(
                                ladder_outcome.omitted_capabilities
                            )
                        if ladder_outcome.banner is not None:
                            extra["banner"] = ladder_outcome.banner
                    effective_route = "semantic"
                    outcome = await _produce_rehydration_message_stream(
                        body,
                        principal,
                        ctx,
                        disconnected,
                        queue,
                        terminal,
                        run_id,
                        ladder_outcome.message,
                        stage_accumulator=stage_models,
                        extra=extra,
                    )
                    trace.get_current_span().set_attribute("effective_route", effective_route)
                    committed = outcome == "completed" and terminal.terminal == "done"
                    return {"outcome": outcome, "query_type": query_type, "committed": committed}
                if isinstance(ladder_outcome, CapabilityUnavailable):
                    if usage_capture is not None and ladder_outcome.bq is not None:
                        fill_terminal_usage_from_bq(usage_capture, ladder_outcome.bq)
                    raise CapabilityUnavailableError(ladder_outcome.detail)
                if isinstance(ladder_outcome, Answered):
                    query_type = ladder_outcome.query_type
                    if ladder_outcome.query_type == "semantic":
                        trace.get_current_span().set_attribute("requested_route", "semantic")
                        effective_route = "semantic"
                        if usage_capture is not None and ladder_outcome.usage is not None:
                            usage_capture.input_tokens = ladder_outcome.usage.input_tokens
                            usage_capture.output_tokens = ladder_outcome.usage.output_tokens
                            usage_capture.reasoning_tokens = ladder_outcome.usage.reasoning_tokens
                            usage_capture.cost_status = ladder_outcome.usage.cost_status
                        outcome = await _produce_semantic_stream(
                            body,
                            principal,
                            ctx,
                            disconnected,
                            queue,
                            terminal,
                            run_id,
                            answered=ladder_outcome,
                            usage=usage_capture,
                            stage_accumulator=stage_models,
                        )
                    elif ladder_outcome.bq is None:
                        trace.get_current_span().set_attribute("requested_route", "semantic")
                        effective_route = "structured"
                        await queue.put(StatusFrame("reading_page"))
                        outcome = await _produce_records_only_stream(
                            body,
                            principal,
                            ctx,
                            disconnected,
                            queue,
                            terminal,
                            run_id,
                            answered=ladder_outcome,
                            stage_accumulator=stage_models,
                        )
                    else:
                        trace.get_current_span().set_attribute("requested_route", query_type)
                        effective_route = query_type
                        outcome = await produce_bq_stream(
                            body=body,
                            principal=principal,
                            ctx=ctx,
                            disconnected=disconnected,
                            queue=queue,
                            terminal=terminal,
                            run_id=run_id,
                            query_type=query_type,
                            stage_accumulator=stage_models,
                            answered=ladder_outcome,
                            usage=usage_capture,
                        )
                    trace.get_current_span().set_attribute("effective_route", effective_route)
                    committed = outcome == "completed" and terminal.terminal == "done"
                    return {"outcome": outcome, "query_type": query_type, "committed": committed}

                raise AssertionError(f"unhandled ask outcome: {type(ladder_outcome)!r}")
        if box.value is not None:
            outcome = box.value
    except UnknownPageContextProfileError as exc:
        await terminal.emit("error", {"error": "invalid_page_context", "detail": str(exc)})
        outcome = "error"
    except (RouteResolutionError, PolicyViolationError) as exc:
        route_failure = ModelRouteUnavailableError(f"model route unavailable: {exc}")
        await terminal.emit(
            "error", report_ask_failure(route_failure, stage="prepare", role=principal.role)
        )
        outcome = "error"
    finally:
        finalize_trace(
            run_tree,
            outcome,
            query_type=query_type,
            committed=committed,
        )
        note_query_record_terminal(
            body=body,
            principal=principal,
            ctx=ctx,
            run_outcome=outcome,
            query_type=query_type,
            started_at=started_at,
            prompt_version=resolve_prompt_version(query_type),
            input_tokens=usage_capture.input_tokens,
            output_tokens=usage_capture.output_tokens,
            reasoning_tokens=usage_capture.reasoning_tokens,
            cost_status=usage_capture.cost_status,
            # Exact None-check, not truthiness: an empty-string model is a real
            # captured value and must not fall through to the default. A BQ turn
            # fills usage_capture.model with the resolved route model (e.g.
            # gpt-5.6-luna); a semantic turn never sets it, so the fallback
            # (settings.active_chat_model) applies when tokens were captured.
            model=(
                usage_capture.model
                if usage_capture.model is not None
                else (
                    settings.active_chat_model if usage_capture.input_tokens is not None else None
                )
            ),
            bq_trace_json=usage_capture.bq_trace_json,
            resolver_disposition=usage_capture.resolver_disposition,
            correlation_id=run_id,
        )
        clear_correlation_id()
        clear_thread_id()
        await terminal.close()
    return {"outcome": outcome, "query_type": query_type, "committed": committed}


async def stream_ask_frames(
    body: Question,
    principal: Principal,
    disconnected: Disconnected,
    *,
    resources: ProcessResources,
) -> AsyncIterator[AskFrame]:
    """Yield one normalized Ask attempt as typed frames."""
    queue: asyncio.Queue[AskFrame | None] = asyncio.Queue()
    terminal = StreamTerminal(queue)
    stop = asyncio.Event()

    async def heartbeats() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except TimeoutError:
                await queue.put(HeartbeatFrame())

    async def run_attempt() -> dict:
        return await with_root_tracing_context(
            lambda: _run_sse_attempt(
                body, principal, disconnected, queue, terminal, resources=resources
            )
        )

    hb_task = asyncio.create_task(heartbeats())
    producer_task = asyncio.create_task(run_attempt())
    try:
        while True:
            frame = await queue.get()
            if frame is None:
                break
            yield frame
    finally:
        stop.set()
        hb_task.cancel()
        if not producer_task.done():
            producer_task.cancel()
        await asyncio.gather(hb_task, producer_task, return_exceptions=True)
