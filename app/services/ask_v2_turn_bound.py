"""Ask v2 whole-turn bound: expiry signal, stream race, and timeout cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from app.auth import Principal
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import TurnBudget, await_with_budget
from app.models.ask_v2_events import StreamErrorEvent
from app.models.ask_v2_request import AskV2Request
from app.models.schemas import Question
from app.services.ask_v2_frames import render_v2_sse_frame
from app.services.ask_v2_reason_copy import copy_for_reason

logger = logging.getLogger(__name__)

T = TypeVar("T")

CauseKind = Literal["queue", "disconnect", "budget", "keep_alive"]

# Proxies close a stream that carries no bytes; this is the longest quiet gap
# the stream allows. The budget timer still wins whenever it is shorter.
KEEP_ALIVE_INTERVAL_SECONDS: float = 15.0


@dataclass(frozen=True)
class StreamRaceResult:
    kind: CauseKind
    event: Any | None = None


def new_expiry_event() -> asyncio.Event:
    return asyncio.Event()


def is_timeout_cause(exc: BaseException, expiry_event: asyncio.Event) -> bool:
    if isinstance(exc, DeadlineExpiredError):
        return True
    return isinstance(exc, asyncio.CancelledError) and expiry_event.is_set()


async def run_operation_with_budget(
    operation: Callable[[], Awaitable[T]],
    budget: TurnBudget,
    expiry_event: asyncio.Event,
) -> T:
    return await await_with_budget(
        operation,
        budget,
        on_budget_expired=expiry_event.set,
    )


def deadline_exceeded_event(*, run_id: str, sequence: int = 1) -> StreamErrorEvent:
    return StreamErrorEvent(
        protocol_version="2",
        run_id=run_id,
        sequence=sequence,
        event_type="stream_error",
        code="deadline_exceeded",
        retryable=True,
        message=copy_for_reason("timeout"),
    )


def render_deadline_exceeded_frame(*, run_id: str, sequence: int = 1) -> str:
    return render_v2_sse_frame(deadline_exceeded_event(run_id=run_id, sequence=sequence))


async def wait_stream_cause(
    *,
    get_task: asyncio.Task[Any],
    disconnect_task: asyncio.Task[Any],
    budget: TurnBudget,
    expiry_event: asyncio.Event,
    keep_alive_seconds: float | None = None,
) -> StreamRaceResult:
    remaining = budget.remaining_seconds
    if remaining <= 0:
        expiry_event.set()
        return StreamRaceResult(kind="budget")

    keep_alive_first = keep_alive_seconds is not None and keep_alive_seconds < remaining
    timer = asyncio.create_task(
        asyncio.sleep(keep_alive_seconds if keep_alive_first else remaining)
    )
    try:
        done, _pending = await asyncio.wait(
            {get_task, disconnect_task, timer},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if get_task in done:
            return StreamRaceResult(kind="queue", event=get_task.result())
        if disconnect_task in done:
            return StreamRaceResult(kind="disconnect")
        if keep_alive_first:
            return StreamRaceResult(kind="keep_alive")
        expiry_event.set()
        return StreamRaceResult(kind="budget")
    finally:
        if not timer.done():
            timer.cancel()
            try:
                await timer
            except asyncio.CancelledError:
                pass


async def cancel_and_join(*tasks: asyncio.Task[Any] | None) -> None:
    for task in tasks:
        if task is None or task.done():
            continue
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def schedule_timeout_query_record(
    *,
    question: str,
    principal: Principal,
    correlation_id: str,
    thread_id: str | None = None,
    request: AskV2Request | None = None,
) -> None:
    from app.query_records.wiring import monotonic_start, schedule_from_terminal

    body = Question(
        question=question or " ",
        thread_id=thread_id or (request.thread_id if request is not None else None),
        run_id=correlation_id,
    )
    schedule_from_terminal(
        body=body,
        principal=principal,
        ctx=None,
        run_outcome="error",
        query_type=None,
        started_at=monotonic_start(),
        correlation_id=correlation_id,
        stable_error_code="timeout",
        timeout=True,
        cancelled=False,
    )


async def persist_timeout_history(
    *,
    question: str,
    principal: Principal,
    thread_id: str | None,
    run_id: str | None = None,
) -> None:
    """Write a timeout transcript marker. Retry once; never change the terminal."""
    from app.config import settings
    from app.conversation.turn import TurnContext
    from app.services import answer_finalize

    if thread_id is None or not settings.conversation_enabled:
        return
    ctx = TurnContext(
        thread_id=thread_id,
        history=[],
        search_query=question,
        original_question=question,
    )
    kwargs = {
        "ctx": ctx,
        "principal": principal,
        "question": question,
        "answer": copy_for_reason("timeout"),
        "follow_up_suggestions": [],
        "bq_digest": answer_finalize.timeout_digest_for_persist(question),
        "run_id": run_id,
    }
    try:
        result = await answer_finalize.persist_and_enrich(**kwargs)
        if result.persisted.exchange_id is not None:
            return
    except Exception:
        logger.exception("timeout transcript persist failed; retrying once")
    try:
        await answer_finalize.persist_and_enrich(**kwargs)
    except Exception:
        logger.exception("timeout transcript persist failed after retry")
