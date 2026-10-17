"""Rebuild a thread's memory from its Query Records when Redis lost it (ADR 0085).

Redis keeps a thread for 30 minutes idle and 4 hours in all (ADR 0028 invariant 5);
the panel shows it for a day. When the Redis copy is gone inside those 4 hours, the
thread's own rows give back the text of each exchange. A thread that started
earlier gets no rebuilt memory, as today. The rebuilt memory is text only: no
earlier tool result, painted value or record reference goes back to the model. It
is written back to Redis with the thread's own start, so the 4 hours still count
from the thread's first turn.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from redis.exceptions import RedisError

from app.auth import Principal
from app.conversation.policy import HistoryPolicy
from app.conversation.transcript import build_exchange_turns
from app.conversation.transcript_models import TranscriptTurn
from app.conversation.transcript_store import ConversationStore
from app.core.errors import TranscriptStoreContentionError
from app.telemetry.context import run_in_thread
from app.telemetry.metrics import record_durable_history_rebuild

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ThreadRow:
    """One kept exchange of a thread, with the time the thread started."""

    exchange_id: str
    operation: str | None
    question: str
    answer: str
    started_at: datetime


class ThreadRowReader(Protocol):
    """Reads one person's kept exchanges of a thread, oldest first."""

    async def __call__(
        self, *, thread_id: str, principal: Principal, limit: int
    ) -> list[ThreadRow]: ...


@dataclass(frozen=True)
class DurableHistorySource:
    """Where the rebuild reads from, and its bounds; built by the composition root."""

    read_rows: ThreadRowReader
    max_thread_age: timedelta
    read_timeout_seconds: float


def history_from_rows(rows: Sequence[ThreadRow]) -> list[TranscriptTurn]:
    """The transcript turns a thread's kept exchanges give back, in the stored bound.

    A result page is not an exchange. A regenerate replaces the exchange before
    it and keeps that exchange's question, because its own row names the literal
    request "regenerate" (app/services/ask_v2_service.py _handle_regenerate). A
    regenerate with no exchange before it in the read has no question and is left
    out. The text is redacted by the transcript's own rule, as a Redis turn is.
    """
    kept: list[ThreadRow] = []
    for row in rows:
        if row.operation == "result_page":
            continue
        if row.operation == "regenerate":
            if not kept:
                continue
            row = replace(row, question=kept.pop().question)
        if row.question.strip():
            kept.append(row)
    turns: list[TranscriptTurn] = []
    for row in kept:
        turns.extend(build_exchange_turns(row.question, row.answer, exchange_id=row.exchange_id))
    return HistoryPolicy.current().trim_for_storage(turns)


async def reseed_from_durable_history(
    store: ConversationStore,
    thread_id: str,
    principal: Principal,
    *,
    source: DurableHistorySource,
    now: datetime | None = None,
) -> list[TranscriptTurn]:
    """Rebuild the thread's memory from its rows and write it back to Redis.

    A failed or slow read leaves the turn without memory; a failed write-back
    still answers this turn with the rebuilt memory.
    """
    policy = HistoryPolicy.current()
    try:
        rows = await asyncio.wait_for(
            source.read_rows(
                thread_id=thread_id, principal=principal, limit=policy.max_stored_turns // 2
            ),
            timeout=source.read_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - memory is optional; the turn still answers
        logger.warning("durable history not read: %s", type(exc).__name__)
        record_durable_history_rebuild(outcome="read_failed")
        return []
    if not rows:
        record_durable_history_rebuild(outcome="empty")
        return []
    started_at = rows[0].started_at
    if (now or datetime.now(UTC)) - started_at > source.max_thread_age:
        record_durable_history_rebuild(outcome="too_old")
        return []
    # Redaction runs Presidio; keep it off the event loop, as the live write does.
    turns = await run_in_thread(history_from_rows, rows)
    if not turns:
        record_durable_history_rebuild(outcome="empty")
        return []
    try:
        await store.append(
            thread_id,
            principal.user_id,
            turns,
            principal.entity_id,
            started_at=started_at.timestamp(),
        )
    except (RedisError, TranscriptStoreContentionError) as exc:
        logger.warning("durable history not written back: %s", type(exc).__name__)
        record_durable_history_rebuild(outcome="write_back_failed")
        return turns
    record_durable_history_rebuild(outcome="rebuilt")
    return turns
