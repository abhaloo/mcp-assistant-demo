"""Ask v2 whole-turn bound: expiry signal, stream race, and timeout cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from app.auth import Principal
from app.conversation.transcript_store import new_exchange_id
from app.core.errors import (
    ConversationStoreUnavailableError,
    DeadlineExpiredError,
    TranscriptStoreContentionError,
)
from app.core.turn_budget import TurnBudget, await_with_budget
from app.models.ask_v2_events import StreamErrorEvent
from app.models.ask_v2_request import AskV2Request
from app.models.schemas import Question
from app.services.ask_v2_frames import render_v2_sse_frame
from app.services.ask_v2_reason_copy import copy_for_reason

if TYPE_CHECKING:
    from app.conversation.evidence.ports import EvidenceSnapshotStore

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


async def write_terminal_snapshot(  # noqa: PLR0913 - the terminal seam's typed payload
    *,
    status: Literal["done", "clarification", "failed", "stopped"],
    restore_ref: str | None,
    exchange_id: str,
    principal: Principal,
    thread_id: str | None,
    run_id: str | None,
    answer_text: str,
    duration_ms: int | None,
    store: EvidenceSnapshotStore | None,
) -> str | None:
    """Write the whole-turn terminal snapshot: the server-vouched restore record.

    Carries the terminal status, the server duration, and the terminal copy; no
    sources, rows, or business query. ``restore_ref=None`` keeps today's
    store-minted reference for callers that never minted their own."""
    from app.models.tool_results import TurnResult
    from app.services.ask_result_projection import (
        build_snapshot_bindings,
        build_snapshot_payload,
        publish_evidence_snapshot,
    )

    if store is None or thread_id is None or run_id is None:
        return None
    payload = build_snapshot_payload(
        thread_id=thread_id,
        run_id=run_id,
        exchange_id=exchange_id,
        answer_text=answer_text,
        turn_result=TurnResult(
            outcome_type="incomplete",
            completeness="none",
            trusted=False,
            selected=(),
            omissions=(),
            components=(),
        ),
        run_status=status,
        duration_ms=duration_ms,
    )
    bindings = build_snapshot_bindings(
        principal=principal,
        thread_id=thread_id,
        run_id=run_id,
        exchange_id=exchange_id,
    )
    return await publish_evidence_snapshot(store, bindings, payload, restore_ref=restore_ref)


async def persist_stopped_history(  # noqa: PLR0913 - the stopped seam's typed payload
    *,
    question: str,
    principal: Principal,
    thread_id: str | None,
    run_id: str | None = None,
    exchange_id: str | None = None,
    restore_ref: str | None = None,
    store: EvidenceSnapshotStore | None = None,
) -> str | None:
    """Write a stopped transcript marker, then the terminal snapshot under the
    attempt reference. The snapshot does not depend on the transcript store:
    a transcript write failure is logged once and the restore record still lands.
    Only the snapshot bindings mint a fallback exchange id; the caller's
    pre-minted exchange id wins when given."""
    from app.config import settings
    from app.conversation.turn import TurnContext
    from app.services import answer_finalize

    if thread_id is None:
        return exchange_id

    # The exchange is pre-minted by the caller when given; the transcript store
    # mints its own otherwise, and the snapshot bindings carry whatever exists.
    ex = exchange_id or new_exchange_id()

    async def _write_stopped_snapshot() -> None:
        await write_terminal_snapshot(
            status="stopped",
            restore_ref=restore_ref,
            exchange_id=ex,
            principal=principal,
            thread_id=thread_id,
            run_id=run_id,
            answer_text=copy_for_reason("cancelled"),
            duration_ms=None,
            store=store if store is not None else _stopped_snapshot_store(),
        )

    if not settings.conversation_enabled:
        # No transcript can carry the marker, so the restore record alone vouches.
        await _write_stopped_snapshot()
        return ex

    ctx = TurnContext(
        thread_id=thread_id,
        history=[],
        search_query=question,
        original_question=question,
    )
    try:
        result = await answer_finalize.persist_and_enrich(
            ctx=ctx,
            principal=principal,
            question=question,
            answer=copy_for_reason("cancelled"),
            follow_up_suggestions=[],
            bq_digest=answer_finalize.stopped_digest_for_persist(question),
            run_id=run_id,
        )
        if exchange_id is None and result.persisted.exchange_id is not None:
            ex = result.persisted.exchange_id
    except (TranscriptStoreContentionError, ConversationStoreUnavailableError):
        logger.warning("stopped transcript persist failed; the restore record still writes")
    await _write_stopped_snapshot()
    return ex


def _stopped_snapshot_store() -> EvidenceSnapshotStore | None:
    """The retention store, or None when retention is not configured."""
    from app.services.evidence_snapshots import snapshot_store_available

    if not snapshot_store_available():
        return None
    try:
        from app.conversation.evidence.composition import build_evidence_snapshot_store
        from app.resources import current_process_resources

        return build_evidence_snapshot_store(current_process_resources())
    except Exception as exc:  # noqa: BLE001 - retention never fails the turn
        logger.warning("stopped snapshot store unavailable: %s", type(exc).__name__)
        return None


def schedule_stopped_terminal(  # noqa: PLR0913 - the disconnect seam's typed payload
    *,
    question: str,
    principal: Principal,
    thread_id: str | None,
    run_id: str,
    exchange_id: str | None = None,
    restore_ref: str,
) -> asyncio.Task[None] | None:
    """Schedule the detached stopped write for a turn whose client is gone.

    The transcript marker and the terminal snapshot run as one detached task,
    bounded by a 2 s ceiling; nothing awaits the task (the client disconnect won).
    The returned task leaves the event loop only when the write has landed or
    the ceiling expired."""
    if thread_id is None:
        return None

    async def _job() -> None:
        from app.services.ask_v2_turn_bound import persist_stopped_history

        try:
            await asyncio.wait_for(
                persist_stopped_history(
                    question=question,
                    principal=principal,
                    thread_id=thread_id,
                    run_id=run_id,
                    exchange_id=exchange_id,
                    restore_ref=restore_ref,
                ),
                timeout=2.0,
            )
        except TimeoutError:
            logger.warning(
                "stopped terminal write exceeded its ceiling: run_id=%s ref=%s",
                run_id,
                restore_ref,
            )
        except Exception:
            logger.exception("stopped terminal write failed: run_id=%s", run_id)

    return asyncio.create_task(_job())
