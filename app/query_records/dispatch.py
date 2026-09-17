"""Fire-and-forget Query Record writer at terminal state.

Form: **fully asynchronous** — ``asyncio.create_task`` schedules the Postgres
insert off the answer critical path. No queue, retry, dead-letter, or disk
buffer; at-most-once with failure counting on unexpected errors.
Duplicate ``correlation_id`` deliveries are ignored (ON CONFLICT DO NOTHING)
and do not increment the failure counter.
"""

from __future__ import annotations

import asyncio
import logging

from opentelemetry.trace import StatusCode

from app.config import settings
from app.db.postgres import session_scope
from app.query_records.builder import build_query_record
from app.query_records.context import TerminalSnapshot
from app.query_records.failure_counter import increment_write_failure
from app.query_records.repository import QueryRecordRepository
from app.telemetry.spans import terminal_persistence_stage_span

logger = logging.getLogger(__name__)

_pending_writes: set[asyncio.Task[None]] = set()


def query_record_store_configured() -> bool:
    return bool(settings.query_record_database_url.strip())


async def _persist_snapshot(snapshot: TerminalSnapshot) -> None:
    record = build_query_record(snapshot)

    async def _insert(record=record) -> None:
        async with session_scope() as session:
            repo = QueryRecordRepository(session)
            await repo.insert(record)

    with terminal_persistence_stage_span(
        correlation_id=record.correlation_id,
        outcome=record.terminal_outcome,
    ) as span:
        try:
            await _insert()
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR)
            span.set_attribute("bq.outcome", "error")
            increment_write_failure()
            logger.warning(
                "Query Record write failed (counted, fail-open)",
                exc_info=True,
            )


def schedule_query_record_write(snapshot: TerminalSnapshot) -> None:
    """Schedule a terminal write when the store is configured."""
    if not query_record_store_configured():
        return
    task = asyncio.create_task(_persist_snapshot(snapshot))
    _pending_writes.add(task)
    task.add_done_callback(_pending_writes.discard)


async def await_pending_writes() -> None:
    """Await in-flight fire-and-forget terminal inserts (this process only).

    Cross-worker races are handled by bounded DB visibility polls in late_writes.
    Failed inserts are logged here so callers are not silently blind; failure
    counting still happens inside ``_persist_snapshot``.
    """
    if not _pending_writes:
        return
    pending = list(_pending_writes)
    results = await asyncio.gather(*pending, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            logger.warning(
                "pending Query Record write surfaced error during await: %s",
                result,
            )


async def reserve_query_record_execution(
    *,
    correlation_id: str,
    project_id: str,
    environment: str,
    question: str,
    requested_route: str | None = None,
) -> None:
    """Reserve execution row in Query Records before execution begins."""
    if not query_record_store_configured():
        return
    try:
        async with session_scope() as session:
            repo = QueryRecordRepository(session)
            await repo.reserve_execution(
                correlation_id=correlation_id,
                project_id=project_id,
                environment=environment,
                question=question,
                requested_route=requested_route,
            )
    except Exception:
        logger.warning("reserve_execution failed (fail-open)", exc_info=True)


async def flush_pending_writes_for_tests() -> None:
    """Test seam — await in-flight fire-and-forget tasks."""
    await await_pending_writes()
