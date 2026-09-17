"""Ask AI protocol version 2 progressive SSE streaming engine."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from pydantic import ValidationError

from app.auth import Principal
from app.business_query.outcomes import BusinessQueryWireOutcome
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
from app.models.ask_v2_events import (
    CitationSetEvent,
    InteractionEvent,
    StreamErrorEvent,
    TextDeltaEvent,
    TurnOutcomeEvent,
)
from app.models.ask_v2_request import AskV2Request
from app.models.citations import CitationsPayload
from app.models.tool_results import TurnResult, tool_result_fields
from app.resources import ProcessResources
from app.services import ask_v2_frames
from app.services.ask_deadline import Deadline, validate_client_deadline
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
from app.services.ask_v2_details import stream_record_details, without_record_details
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
from app.services.business_query_publication import publish_committed_envelopes
from app.services.byte_bounded_ask_queue import ByteBoundedAskQueue
from app.services.stream_transport import chunk_answer_tokens
from app.telemetry.correlation import bind_thread_id, normalize_run_id
from app.telemetry.invocation_payload import (
    ExecutionIdentity,
    InvocationExpectation,
    TerminalEvidenceError,
    require_terminal_evidence,
)

logger = logging.getLogger(__name__)

_normalize_run_id = normalize_run_id

_DISCONNECT_POLL_INTERVAL_S: float = 0.1


class _V2ServiceLike(Protocol):
    """Structural shape of the v2 service this module drives.

    A Protocol instead of an import of the concrete ``AskV2Service`` keeps
    this module free of a runtime dependency back on
    ``app.services.ask_v2_service``, which itself imports this module to run
    a stream.
    """

    async def _reserve_execution_if_configured(
        self, *, correlation_id: str, question: str
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
    ) -> dict[str, Any]: ...


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


def _wire_outcome(res: dict[str, Any]) -> BusinessQueryWireOutcome | None:
    """The typed Business Query outcome for this turn, if it produced one.

    The operation handlers dump the typed answer before this layer sees it, so
    the outcome arrives as a mapping and is re-validated here rather than
    forwarded as a foreign shape.
    """
    business_query = res.get("business_query")
    if not isinstance(business_query, dict):
        return None
    try:
        return BusinessQueryWireOutcome.model_validate(business_query)
    except ValidationError:
        # An outcome that does not validate is not evidence. Dropping it costs
        # the trust drawer; forwarding it would show an unverified seal.
        logger.warning("v2 terminal outcome dropped: business query wire outcome invalid")
        return None


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


def _persisted_thread_id(res: dict[str, Any], request_thread_id: str | None) -> str | None:
    persisted = res.get("thread_id")
    if isinstance(persisted, str) and persisted:
        return persisted
    return request_thread_id


async def _emit_clarification_card(
    *,
    res: dict[str, Any],
    request: AskV2Request,
    seq: TurnSequence,
    queue: ByteBoundedAskQueue,
    sink: AskV2ProgressSink | NullProgressSink,
    deadline: Deadline,
    wire: BusinessQueryWireOutcome | None = None,
) -> None:
    """Emit clarification interaction card and terminal outcome."""
    choices_raw = None
    prompt_text = None
    allow_free_text = True
    continuation_kind = None

    if wire is not None:
        choices_raw = wire.choices
        prompt_text = wire.prompt
        if wire.allow_free_text is not None:
            allow_free_text = wire.allow_free_text
        continuation_kind = wire.continuation
    else:
        bq = res.get("business_query")
        if isinstance(bq, dict):
            choices_raw = bq.get("choices")
            prompt_text = bq.get("prompt")
            if "allow_free_text" in bq:
                allow_free_text = bq["allow_free_text"]
            continuation_kind = bq.get("continuation")

    if not choices_raw:
        choices_raw = res.get("choices")
    if not prompt_text:
        prompt_text = res.get("prompt") or "Please select an option"
    if not continuation_kind:
        continuation_kind = res.get("continuation")

    options = _normalize_interaction_options(choices_raw)

    correlation_id = normalize_run_id(request.run_id)
    continuation_ref = res.get("continuation_ref")
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
        return

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
    turn_result = res.get("turn_result")
    if not isinstance(turn_result, TurnResult):
        turn_result = None
    tf = (
        tool_result_fields(turn_result, restore_ref=res.get("restore_ref"))
        if turn_result is not None
        else {}
    )
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
        query_record_ref=res.get("query_record_ref"),
        **tf,
    )
    queue.put_nowait(outcome)
    seq.take()


async def _produce_v2_stream_events(
    request: AskV2Request,
    principal: Principal,
    resources: ProcessResources,
    deadline: Deadline,
    queue: ByteBoundedAskQueue,
    service: _V2ServiceLike,
    expiry_event: asyncio.Event | None = None,
) -> None:
    """Background producer: drives execution and enqueues typed v2 events."""
    seq = TurnSequence()
    sink: AskV2ProgressSink | NullProgressSink = NullProgressSink()
    # Query-record writes run inside this producer task; bind the client
    # thread id here so the record stamps the conversation it belongs to.
    bind_thread_id(request.thread_id)
    try:
        # With activity events off the sink still measures the turn, so the
        # receipt on the terminal event does not depend on the flag.
        sink = (
            AskV2ProgressSink(queue, request.run_id, seq)
            if settings.ask_activity_events_enabled
            else NullProgressSink(queue, request.run_id, seq)
        )

        # Check deadline before stage execution
        deadline.check_not_expired()

        # Execute operation
        res = await service.ask(
            request,
            principal,
            resources=resources,
            deadline=deadline,
            progress=sink,
            readiness_already_checked=True,
            expiry_event=expiry_event,
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

        # Check if clarification is required
        bq_val = res.get("business_query")
        is_clarification = res.get("outcome") == "clarification_required" or (
            isinstance(bq_val, dict) and bq_val.get("outcome") == "clarification_required"
        )

        if is_clarification:
            await _emit_clarification_card(
                res=res,
                request=request,
                seq=seq,
                queue=queue,
                sink=sink,
                deadline=deadline,
            )
            return

        # Validate wire outcome once at the top of the terminal path
        wire = _wire_outcome(res)
        envelopes = _result_envelopes(res, wire=wire)
        aqid: str | None = res.get("aqid") or res.get("answer_query_id")

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
        turn_result = res.get("turn_result")
        if not isinstance(turn_result, TurnResult):
            turn_result = None
        outcome_type = _terminal_disposition(res, wire=wire, turn_result=turn_result)
        reason_code, reason_message = _refusal_reason(res, outcome_type)
        first_envelope = _as_envelope_mapping(envelopes[0]) if envelopes else None
        answer_text = (
            reason_message
            or res.get("answer")
            or (first_envelope.get("answer_text") if first_envelope else None)
            or res.get("answer_text")
            or ""
        )
        content_kind = _content_kind(table_streamed=table_streamed)
        sink.finish_thought()
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

        if res.get("evidence_digest"):
            evidence_digest = res["evidence_digest"]
        else:
            correlation_id = normalize_run_id(request.run_id)
            is_fixed = (
                request.operation == "result_page"
                or res.get("model") == FIXED_RESPONSE_MODEL_SENTINEL
                or res.get("outcome") == "clarification_required"
                or res.get("model") is None
            )
            receipt = await require_terminal_evidence(
                ExecutionIdentity(correlation_id=correlation_id),
                InvocationExpectation(min_invocations=0 if is_fixed else 1),
                deadline=deadline,
            )
            evidence_digest = receipt.evidence_digest or res.get("digest")

        # A step the turn cut off must not seal as completed: a timed-out
        # planner showing a green check reads as finished work.
        duration_ms = sink.fail() if reason_code == "timeout" else sink.finish()
        follow_ups = _follow_up_actions(res, outcome_type)

        # The citation set is part of the version 1 projection: legacy turns
        # keep their sources on the terminal payload only (spec §8).
        sources = res.get("sources") or []
        citations = res.get("citations")
        if turn_result is not None and (sources or citations):
            queue.put_nowait(
                CitationSetEvent(
                    protocol_version="2",
                    run_id=request.run_id,
                    sequence=seq.peek(),
                    event_type="citation_set",
                    sources=list(sources),
                    citations=(
                        citations
                        if isinstance(citations, CitationsPayload)
                        else CitationsPayload(parsed=False)
                    ),
                )
            )
            seq.take()

        tf = (
            tool_result_fields(turn_result, restore_ref=res.get("restore_ref"))
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
                turn_result.trusted
                if turn_result is not None
                else (bool(res.get("trusted", True)) and outcome_type == "answered")
            ),
            thread_id=_persisted_thread_id(res, request.thread_id),
            duration_ms=duration_ms,
            follow_ups=follow_ups,
            explanation=_explanation(res),
            answer_query_id=aqid,
            evidence_digest=evidence_digest,
            query_record_ref=res.get("query_record_ref"),
            reason_code=reason_code,
            message=reason_message,
            business_query=None if wire is None else without_record_details(wire),
            answer_mode=res.get("answer_mode"),
            source_exchange_ids=list(res.get("source_exchange_ids") or []),
            **tf,
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
    finally:
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
        new_expiry_event,
        persist_timeout_history,
        render_deadline_exceeded_frame,
        run_operation_with_budget,
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

    producer_task = asyncio.create_task(
        _produce_v2_stream_events(
            request=request,
            principal=principal,
            resources=resources,
            deadline=deadline,
            queue=queue,
            service=service,
            expiry_event=signal,
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


# Canonical Ask stream alias
stream_ask_events = stream_ask_v2_events
