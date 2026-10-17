"""Ask AI protocol version 2 progressive SSE streaming engine."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from typing import Any, Protocol

from app.auth import Principal
from app.business_query.ports import BusinessProgressSink
from app.business_query.wire.module import PLANNER_TIMEOUT_CONTINUATION
from app.config import settings
from app.core.errors import (
    ContinuationClaimRejectedError,
    DeadlineExpiredError,
    NotFoundError,
    QueueBufferExceededError,
    ServiceUnavailableError,
)
from app.models.ask_response import Answer
from app.models.ask_v2_events import (
    CitationSetEvent,
    InteractionEvent,
    StreamErrorEvent,
    TextDeltaEvent,
    TurnAcceptedEvent,
    TurnOutcomeEvent,
)
from app.models.ask_v2_request import AskV2Request
from app.models.schemas import Question
from app.models.tool_results import tool_result_fields
from app.query_records.turn_content import TurnContent
from app.resources import ProcessResources
from app.services import ask_v2_frames
from app.services.ask_deadline import Deadline, validate_client_deadline
from app.services.ask_observation import (
    DeferredTerminalWriter,
    TerminalIdentity,
    TerminalWriter,
)
from app.services.ask_result_projection import (
    as_envelope_mapping as _as_envelope_mapping,
)
from app.services.ask_result_projection import (
    content_kind as _content_kind,
)
from app.services.ask_result_projection import (
    explanation_from_provenance as _explanation,
)
from app.services.ask_result_projection import (
    follow_up_actions as _follow_up_actions,
)
from app.services.ask_result_projection import (
    normalize_interaction_options as _normalize_interaction_options,
)
from app.services.ask_result_projection import (
    refusal_reason as _refusal_reason,
)
from app.services.ask_result_projection import (
    result_envelopes as _result_envelopes,
)
from app.services.ask_result_projection import (
    terminal_disposition as _terminal_disposition,
)
from app.services.ask_v2_details import stream_record_details, terminal_form
from app.services.ask_v2_frames import (
    KEEP_ALIVE_FRAME,
    SequenceOutcome,
    V2EventSequenceValidator,
    render_v2_sse_frame,
)

if "citation_set" not in ask_v2_frames.KNOWN_EVENT_TYPES:
    ask_v2_frames.KNOWN_EVENT_TYPES = frozenset(ask_v2_frames.KNOWN_EVENT_TYPES | {"citation_set"})
from app.providers.stage_model_report import FIXED_RESPONSE_MODEL_SENTINEL
from app.services.ask_v2_progress import (
    AskV2ProgressSink,
    NullProgressSink,
    TurnSequence,
)
from app.services.ask_v2_reason_copy import copy_for_reason
from app.services.ask_v2_result import (
    ClarificationCard,
    V2Reply,
    V2Result,
    is_clarification,
)
from app.services.business_query_publication import publish_committed_envelopes
from app.services.byte_bounded_ask_queue import ByteBoundedAskQueue
from app.services.evidence_snapshots import bind_turn_timeline
from app.services.feedback_tokens import make_feedback_token
from app.services.stream_transport import chunk_answer_tokens
from app.telemetry.correlation import (
    bind_restore_ref,
    bind_thread_id,
    current_restore_ref,
    normalize_run_id,
)
from app.telemetry.invocation_payload import (
    ExecutionIdentity,
    InvocationExpectation,
    TerminalEvidenceError,
    require_terminal_evidence,
)

logger = logging.getLogger(__name__)

_normalize_run_id = normalize_run_id

_DISCONNECT_POLL_INTERVAL_S: float = 0.1

# The attempts a cancelled client's stream schedules in the background. The
# generator's return wipes its locals; the set keeps the write alive.
_DETACHED_WRITES: set[asyncio.Task[None]] = set()


class _V2ServiceLike(Protocol):
    """Structural shape of the v2 service this module drives.

    A Protocol instead of an import of the concrete ``AskV2Service`` keeps
    this module free of a runtime dependency back on
    ``app.services.ask_v2_service``, which itself imports this module to run
    a stream.
    """

    async def _reserve_execution_if_configured(
        self,
        *,
        correlation_id: str,
        question: str,
        thread_id: str | None = None,
        principal: Principal | None = None,
    ) -> None: ...

    async def ask(
        self,
        request: AskV2Request,
        principal: Principal,
        *,
        resources: ProcessResources,
        deadline: Deadline,
        progress: BusinessProgressSink | None = None,
        readiness_already_checked: bool = False,
        expiry_event: asyncio.Event | None = None,
        terminal: TerminalWriter | None = None,
    ) -> V2Reply: ...


async def _watch_disconnect(disconnected: Callable[[], Awaitable[bool]]) -> None:
    """Resolve once the client disconnects, without touching the item queue."""
    while not await disconnected():
        await asyncio.sleep(_DISCONNECT_POLL_INTERVAL_S)


def _emit_stream_error(
    queue: ByteBoundedAskQueue,
    seq: TurnSequence,
    run_id: str,
    code: str,
    message: str,
    *,
    retryable: bool,
    sink: AskV2ProgressSink | NullProgressSink | None = None,
) -> None:
    """Emit a typed stream error frame onto the wire queue."""
    if sink is not None:
        sink.fail()
    err_ev = StreamErrorEvent(
        protocol_version="2",
        run_id=run_id,
        sequence=seq.take(),
        event_type="stream_error",
        code=code,
        retryable=retryable,
        message=message,
    )
    queue.put_nowait(err_ev)


# Stable wire code per unavailable-dependency outcome. An unlisted subclass
# reports as a capability gap, which is the honest generic reading of "this
# deployment cannot do that right now".
_UNAVAILABLE_CODES = {
    "ResultPageDisabledError": "result_page_disabled",
    "CapabilityUnavailableError": "capability_unavailable",
    "DocumentUnavailableError": "document_unavailable",
    "ContinuationUnavailableError": "continuation_unavailable",
    "ModelRouteUnavailableError": "model_route_unavailable",
    "CircuitOpenError": "upstream_circuit_open",
    "ConversationStoreUnavailableError": "conversation_store_unavailable",
    "TranscriptStoreContentionError": "transcript_store_busy",
}


async def _emit_clarification_card(
    *,
    res: V2Result,
    request: AskV2Request,
    principal: Principal,
    seq: TurnSequence,
    queue: ByteBoundedAskQueue,
    sink: AskV2ProgressSink | NullProgressSink,
    deadline: Deadline,
) -> str | None:
    """Emit the clarification card and terminal outcome; return the question shown."""
    if isinstance(res, ClarificationCard):
        choices_raw, prompt_text = res.choices, res.prompt
        allow_free_text, continuation_kind = res.allow_free_text, res.continuation
        continuation_ref, turn_result = res.continuation_ref, None
    else:
        # An answer whose own query asked back carries no ticket, so it cannot
        # paint a card; the missing ticket below ends it as an internal error.
        wire = res.business_query
        choices_raw = list(wire.choices) if wire is not None else []
        continuation_kind = wire.continuation if wire is not None else None
        prompt_text, allow_free_text = None, True
        continuation_ref, turn_result = None, res.turn_result
    prompt_text = prompt_text or "Please select an option"

    options = _normalize_interaction_options(choices_raw)

    correlation_id = normalize_run_id(request.run_id)
    if not continuation_ref:
        _emit_stream_error(
            queue,
            seq,
            request.run_id,
            "internal_error",
            "Something went wrong on our side. Please try that again.",
            retryable=True,
            sink=sink,
        )
        return None

    deadline.check_not_expired()
    await require_terminal_evidence(
        ExecutionIdentity(correlation_id=correlation_id),
        InvocationExpectation(min_invocations=0),
        deadline=deadline,
    )

    interaction = InteractionEvent(
        protocol_version="2",
        run_id=request.run_id,
        sequence=seq.peek(),
        event_type="interaction",
        interaction_kind="clarification",
        continuation_ref=continuation_ref,
        prompt=prompt_text,
        options=options,
        free_text_allowed=allow_free_text,
    )
    queue.put_nowait(interaction)
    seq.take()

    duration_ms = (
        sink.fail() if continuation_kind == PLANNER_TIMEOUT_CONTINUATION else sink.finish()
    )
    tf = tool_result_fields(turn_result, restore_ref=None) if turn_result is not None else {}
    outcome = TurnOutcomeEvent(
        protocol_version="2",
        run_id=request.run_id,
        sequence=seq.peek(),
        event_type="turn_outcome",
        outcome_type="clarification_required",
        trusted=False,
        thread_id=request.thread_id,
        duration_ms=duration_ms,
        answer_query_id=None,
        evidence_digest=None,
        query_record_ref=None,
        unanswered_part=None,
        **tf,
        feedback_token=make_feedback_token(request.run_id, principal),
    )
    queue.put_nowait(outcome)
    seq.take()
    return prompt_text


async def _produce_v2_stream_events(
    request: AskV2Request,
    principal: Principal,
    resources: ProcessResources,
    deadline: Deadline,
    queue: ByteBoundedAskQueue,
    service: _V2ServiceLike,
    expiry_event: asyncio.Event | None = None,
    *,
    restore_ref: str | None = None,
) -> None:
    """Background producer: drives execution and enqueues typed v2 events."""
    seq = TurnSequence()
    sink: AskV2ProgressSink | NullProgressSink = NullProgressSink()
    # Query-record writes run inside this producer task; bind the client
    # thread id here so the record stamps the conversation it belongs to.
    bind_thread_id(request.thread_id)
    # The answer path publishes its snapshot under the reference the stream
    # already announced; context vars are task-local, so the generator's
    # stack never carries it. A producer driven without one mints it once,
    # so the frame and the bound reference are the same value.
    restore_ref = restore_ref or secrets.token_urlsafe(32)
    bind_restore_ref(restore_ref)
    # The deferred terminal writer commits the ledger row after the last frame, exists
    # before the first frame, and is seeded with the identity so a cancel still writes it.
    started_at = time.monotonic()
    correlation_id = normalize_run_id(request.run_id)
    identity = TerminalIdentity(
        body=Question(
            question=request.question or " ",
            thread_id=request.thread_id,
            page_context=request.page_context,
            record_context=request.record_context,
            operation="new_question",
            run_id=correlation_id,
            idempotency_key=request.idempotency_key,
            response_policy=request.response_policy or "allow_partial",
        ),
        principal=principal,
        started_at=started_at,
        correlation_id=correlation_id,
    )
    # What the person saw. The writer reads it, with the steps shown so far, when
    # it commits: after the last frame, on a stop, or on a failure.
    content = TurnContent(operation=request.operation)

    def shown_turn() -> TurnContent:
        return replace(content, timeline=sink.timeline())

    writer = DeferredTerminalWriter(identity, read_turn_content=shown_turn)
    try:
        # The first frame carries the attempt identity, before the first
        # interruptible moment: a turn interrupted at any later point keeps
        # its reference so the restore readback cannot see a second one.
        accepted = TurnAcceptedEvent(
            protocol_version="2",
            run_id=request.run_id,
            sequence=seq.peek(),
            event_type="turn_accepted",
            thread_id=request.thread_id,
            restore_ref=restore_ref,
        )
        queue.put_nowait(accepted)
        seq.take()

        # With activity events off the sink still measures the turn, so the
        # receipt on the terminal event does not depend on the flag.
        sink = (
            AskV2ProgressSink(queue, request.run_id, seq)
            if settings.ask_activity_events_enabled
            else NullProgressSink(queue, request.run_id, seq)
        )
        # The snapshot written inside the answer path keeps what this sink showed.
        bind_turn_timeline(sink.timeline)

        # Check deadline before stage execution
        deadline.check_not_expired()

        # Execute operation
        reply = await service.ask(
            request,
            principal,
            resources=resources,
            deadline=deadline,
            progress=sink,
            readiness_already_checked=True,
            expiry_event=expiry_event,
            terminal=writer,
        )

        if getattr(sink, "disclosure_violation", False):
            _emit_stream_error(
                queue,
                seq,
                request.run_id,
                "adapter_invalid",
                copy_for_reason(
                    "adapter_invalid",
                    "I could not authorize that answer set, so I am not showing it.",
                ),
                retryable=False,
                sink=sink,
            )
            return

        res = reply.result
        # The isinstance term lets the type checker narrow res to Answer below.
        if isinstance(res, ClarificationCard) or is_clarification(res):
            asked = await _emit_clarification_card(
                res=res,
                request=request,
                principal=principal,
                seq=seq,
                queue=queue,
                sink=sink,
                deadline=deadline,
            )
            exchange_id = res.exchange_id if isinstance(res, Answer) else None
            content = replace(content, exchange_id=exchange_id, answer_text=asked)
            return

        wire = res.business_query
        envelopes = _result_envelopes(res)
        aqid: str | None = None

        await publish_committed_envelopes(envelopes, sink, deadline)
        if getattr(sink, "disclosure_violation", False):
            _emit_stream_error(
                queue,
                seq,
                request.run_id,
                "adapter_invalid",
                copy_for_reason(
                    "adapter_invalid",
                    "I could not authorize that answer set, so I am not showing it.",
                ),
                retryable=False,
                sink=sink,
            )
            return

        table_streamed = bool(getattr(sink, "painted_ordinals", None))
        if envelopes:
            first_env = _as_envelope_mapping(envelopes[0])
            if first_env and first_env.get("answer_query_id") and (aqid is None):
                aqid = first_env["answer_query_id"]

        # Detail facts follow every table and precede the text, so the
        # terminal frame stays within the frame bound whatever their count.
        if wire is not None:
            for envelope in wire.envelopes or (
                [wire.envelope] if wire.envelope is not None else []
            ):
                stream_record_details(queue, seq, request.run_id, envelope)

        # Stream text delta events progressively
        turn_result = res.turn_result
        outcome_type = _terminal_disposition(res)
        reason_code, reason_message = _refusal_reason(res, outcome_type)
        first_envelope = _as_envelope_mapping(envelopes[0]) if envelopes else None
        answer_text = (
            reason_message
            or res.answer
            or (first_envelope.get("answer_text") if first_envelope else None)
            or ""
        )
        content_kind = _content_kind(table_streamed=table_streamed, producer_kind=res.text_kind)
        sink.finish_thought()

        # The citation set precedes the answer text so a [N] marker can
        # resolve while the answer streams. It rides the v2 projection only:
        # a legacy turn keeps its sources on the terminal payload.
        sources = res.sources
        citations = res.citations
        if turn_result is not None and (sources or citations):
            queue.put_nowait(
                CitationSetEvent(
                    protocol_version="2",
                    run_id=request.run_id,
                    sequence=seq.peek(),
                    event_type="citation_set",
                    sources=list(sources),
                    citations=citations,
                )
            )
            seq.take()

        if answer_text:
            chunks = chunk_answer_tokens(answer_text, max_frames=30)
            for chunk in chunks:
                delta_ev = TextDeltaEvent(
                    protocol_version="2",
                    run_id=request.run_id,
                    sequence=seq.peek(),
                    event_type="text_delta",
                    delta=chunk,
                    content_kind=content_kind,
                )
                queue.put_nowait(delta_ev)
                seq.take()

        # Check deadline and execute terminal evidence barrier
        deadline.check_not_expired()

        if reply.evidence_digest:
            evidence_digest = reply.evidence_digest
        else:
            correlation_id = normalize_run_id(request.run_id)
            is_fixed = (
                request.operation == "result_page" or res.model == FIXED_RESPONSE_MODEL_SENTINEL
            )
            receipt = await require_terminal_evidence(
                ExecutionIdentity(correlation_id=correlation_id),
                InvocationExpectation(min_invocations=0 if is_fixed else 1),
                deadline=deadline,
            )
            evidence_digest = receipt.evidence_digest

        # A step the turn cut off must not seal as completed: a timed-out
        # planner showing a green check reads as finished work.
        duration_ms = sink.fail() if reason_code == "timeout" else sink.finish()
        follow_ups = _follow_up_actions(res, outcome_type)

        tf = (
            tool_result_fields(
                turn_result,
                # One reference on the wire: the attempt the first frame named.
                restore_ref=current_restore_ref(),
            )
            if turn_result is not None
            else {}
        )
        outcome = TurnOutcomeEvent(
            protocol_version="2",
            run_id=request.run_id,
            sequence=seq.peek(),
            event_type="turn_outcome",
            outcome_type=outcome_type,
            # Only an answered turn may be trusted. Anything else is a refusal
            # or a partial result and carries no trust regardless of what the
            # producing layer put in the result body.
            trusted=(
                turn_result.trusted if turn_result is not None else outcome_type == "answered"
            ),
            thread_id=res.thread_id or request.thread_id,
            duration_ms=duration_ms,
            follow_ups=follow_ups,
            explanation=_explanation(res),
            answer_query_id=aqid,
            evidence_digest=evidence_digest,
            query_record_ref=None,
            reason_code=reason_code,
            message=reason_message,
            business_query=None if wire is None else terminal_form(wire),
            answer_mode=res.answer_mode,
            source_exchange_ids=list(res.source_exchange_ids),
            budget=res.budget,
            unanswered_part=res.unanswered_part,
            ui_links=res.ui_links or [],
            **tf,
            feedback_token=make_feedback_token(request.run_id, principal),
        )
        content = replace(
            content,
            exchange_id=res.exchange_id,
            answer_text=answer_text or None,
            turn_result=turn_result,
            follow_ups=tuple(follow_ups),
            unanswered_part=res.unanswered_part,
        )
        queue.put_nowait(outcome)
        seq.take()

    except (ContinuationClaimRejectedError, NotFoundError) as exc:
        code = (
            "continuation_rejected"
            if isinstance(exc, ContinuationClaimRejectedError)
            else "continuation_expired"
        )
        _emit_stream_error(
            queue,
            seq,
            request.run_id,
            code,
            copy_for_reason(code),
            retryable=False,
            sink=sink,
        )
    except ServiceUnavailableError as exc:
        code = _UNAVAILABLE_CODES.get(type(exc).__name__, "capability_unavailable")
        logger.warning(
            "Ask AI v2 unavailable: code=%s error=%s run_id=%s",
            code,
            type(exc).__name__,
            request.run_id,
        )
        _emit_stream_error(
            queue,
            seq,
            request.run_id,
            code,
            copy_for_reason(code, str(exc)),
            retryable=False,
            sink=sink,
        )
    except (DeadlineExpiredError, TimeoutError):
        _emit_stream_error(
            queue,
            seq,
            request.run_id,
            "deadline_exceeded",
            copy_for_reason("timeout"),
            retryable=True,
            sink=sink,
        )
    except QueueBufferExceededError as exc:
        logger.warning("Ask AI v2 stream buffer exceeded: %s", exc)
        writer.commit(run_outcome="error", stable_error_code="buffer_exceeded")
        _emit_stream_error(
            queue,
            seq,
            request.run_id,
            "buffer_exceeded",
            "That answer was larger than the panel can stream. Ask for fewer rows.",
            retryable=False,
            sink=sink,
        )
    except TerminalEvidenceError:
        logger.exception("Ask AI v2 stream terminal evidence missing")
        _emit_stream_error(
            queue,
            seq,
            request.run_id,
            "evidence_missing",
            "I could not record that answer, so I am not showing it. Please ask again.",
            retryable=True,
            sink=sink,
        )
    except Exception:
        logger.exception("Ask AI v2 stream producer error")
        _emit_stream_error(
            queue,
            seq,
            request.run_id,
            "internal_error",
            "Something went wrong on our side. Please try that again.",
            retryable=True,
            sink=sink,
        )
    except asyncio.CancelledError:
        # A cancel before the attempt recorded anything is a stop; a recorded
        # terminal (a budget timeout, an error) keeps its own outcome.
        writer.commit(fallback_outcome="stopped")
        raise
    finally:
        # Every run commits exactly once: the idempotent commit here covers
        # the answered, clarification and error paths already handled above.
        writer.commit()
        queue.close()


async def stream_ask_v2_events(
    request: AskV2Request,
    principal: Principal,
    disconnected: Callable[[], Awaitable[bool]],
    *,
    resources: ProcessResources,
    deadline: Deadline | None = None,
    service: _V2ServiceLike,
    expiry_event: asyncio.Event | None = None,
) -> AsyncIterator[str]:
    """Stream progressive Ask AI v2 SSE frames with bounded queue and deadline enforcement."""
    from app.services.ask_v2_turn_bound import (
        KEEP_ALIVE_INTERVAL_SECONDS,
        cancel_and_join,
        new_exchange_id,
        new_expiry_event,
        persist_timeout_history,
        render_deadline_exceeded_frame,
        run_operation_with_budget,
        schedule_stopped_terminal,
        schedule_timeout_query_record,
        wait_stream_cause,
    )

    if await disconnected():
        return

    from app.services.ask_v2_gate_c import check_gate_c_readiness

    gate_c = await check_gate_c_readiness(resources)
    if not gate_c.is_ready:
        err_ev = StreamErrorEvent(
            protocol_version="2",
            run_id=request.run_id,
            sequence=1,
            event_type="stream_error",
            code="gate_c_blocked",
            retryable=False,
        )
        yield render_v2_sse_frame(err_ev)
        return

    if deadline is None:
        server_now_ms = int(time.time() * 1000)
        try:
            deadline = validate_client_deadline(request.deadline_at_ms, server_now_ms=server_now_ms)
        except Exception:
            err_ev = StreamErrorEvent(
                protocol_version="2",
                run_id=request.run_id,
                sequence=1,
                event_type="stream_error",
                code="deadline_exceeded",
                retryable=True,
            )
            yield render_v2_sse_frame(err_ev)
            return

    signal = expiry_event if expiry_event is not None else new_expiry_event()
    correlation_id = normalize_run_id(request.run_id)
    reservation_attempted = False
    timeout_question = request.question or " "

    async def _close_timeout_reservation() -> None:
        schedule_timeout_query_record(
            question=timeout_question,
            principal=principal,
            correlation_id=correlation_id,
            request=request,
        )
        await persist_timeout_history(
            question=timeout_question,
            principal=principal,
            thread_id=request.thread_id,
            run_id=correlation_id,
        )

    try:
        reservation_attempted = True
        await run_operation_with_budget(
            lambda: service._reserve_execution_if_configured(
                correlation_id=correlation_id,
                question=request.question or "",
                thread_id=request.thread_id,
                principal=principal,
            ),
            deadline,
            signal,
        )
    except DeadlineExpiredError:
        await _close_timeout_reservation()
        yield render_deadline_exceeded_frame(run_id=request.run_id)
        return

    queue = ByteBoundedAskQueue()
    validator = V2EventSequenceValidator(initial_sequence=1)

    # The generator owns the attempt identity, not the producer task: the
    # first frame names it, and the detached stopped write reuses it when the
    # client disconnects mid-turn.
    restore_ref = secrets.token_urlsafe(32)
    exchange_id = new_exchange_id()

    producer_task = asyncio.create_task(
        _produce_v2_stream_events(
            request=request,
            principal=principal,
            resources=resources,
            deadline=deadline,
            queue=queue,
            service=service,
            expiry_event=signal,
            restore_ref=restore_ref,
        )
    )
    disconnect_task = asyncio.create_task(_watch_disconnect(disconnected))
    get_task: asyncio.Task[Any] | None = None
    terminal_cause: str | None = None

    try:
        while True:
            if get_task is None:
                get_task = asyncio.create_task(queue.get())

            race = await wait_stream_cause(
                get_task=get_task,
                disconnect_task=disconnect_task,
                budget=deadline,
                expiry_event=signal,
                keep_alive_seconds=KEEP_ALIVE_INTERVAL_SECONDS,
            )

            if race.kind == "disconnect":
                terminal_cause = "disconnect"
                await cancel_and_join(producer_task, get_task)
                break

            if race.kind == "budget":
                terminal_cause = "budget"
                await cancel_and_join(producer_task, get_task, disconnect_task)
                if reservation_attempted:
                    await _close_timeout_reservation()
                yield render_deadline_exceeded_frame(run_id=request.run_id)
                break

            if race.kind == "keep_alive":
                yield KEEP_ALIVE_FRAME
                continue

            event = race.event
            get_task = None

            if event is None:
                break

            if terminal_cause is not None:
                continue

            outcome = validator.validate_next(event)
            if outcome is SequenceOutcome.IGNORE:
                continue

            if outcome is SequenceOutcome.REJECT:
                await cancel_and_join(producer_task)
                err_ev = StreamErrorEvent(
                    protocol_version="2",
                    run_id=request.run_id,
                    sequence=event.sequence,
                    event_type="stream_error",
                    code="sequence_violation",
                    retryable=False,
                )
                yield render_v2_sse_frame(err_ev)
                break

            yield render_v2_sse_frame(event)

            if event.event_type in ("turn_outcome", "stream_error"):
                terminal_cause = event.event_type
                break
    finally:
        await cancel_and_join(disconnect_task, get_task, producer_task)
        if terminal_cause not in ("turn_outcome", "stream_error", "budget"):
            # The attempt ended without a terminal frame: write the stopped
            # terminal under the reference the first frame carried. Nothing awaits it.
            stopped_write = schedule_stopped_terminal(
                question=timeout_question,
                principal=principal,
                thread_id=request.thread_id,
                run_id=correlation_id,
                exchange_id=exchange_id,
                restore_ref=restore_ref,
            )
            if stopped_write is not None:
                _DETACHED_WRITES.add(stopped_write)
                stopped_write.add_done_callback(_DETACHED_WRITES.discard)


# Canonical Ask stream alias
stream_ask_events = stream_ask_v2_events
