"""Late-write paths for feedback, client timings, and eval joins (S1b)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.db.postgres import session_scope
from app.query_records.dispatch import await_pending_writes, query_record_store_configured
from app.query_records.failure_counter import increment_write_failure
from app.query_records.repository import FeedbackTargetMissingError, QueryRecordRepository
from app.query_records.types import EvaluationUpdate, FeedbackUpdate, TimingUpdate

logger = logging.getLogger(__name__)

# Bounded wait for terminal insert visibility (same-process tasks + mild replica lag).
_VISIBILITY_BACKOFF_S = (0.0, 0.02, 0.05, 0.1)


async def _fail_open_late_write(
    *,
    operation: str,
    correlation_id: str,
    write: Callable[[QueryRecordRepository], Awaitable[None]],
) -> bool:
    """Late write — returns False when the store is off, row missing, or DB errors."""
    if not query_record_store_configured():
        return False

    async def _attempt() -> None:
        async with session_scope() as session:
            repo = QueryRecordRepository(session)
            await write(repo)

    await await_pending_writes()
    last_missing = False
    for delay in _VISIBILITY_BACKOFF_S:
        if delay:
            await asyncio.sleep(delay)
        try:
            await _attempt()
            return True
        except FeedbackTargetMissingError:
            last_missing = True
            # Re-await local fire-and-forget tasks between polls (multi-attempt).
            await await_pending_writes()
            continue
        except Exception:
            increment_write_failure()
            logger.warning(
                "%s late-write failed (counted, fail-open) correlation_id=%s",
                operation,
                correlation_id,
                exc_info=True,
            )
            return False

    if last_missing:
        logger.info(
            "%s late-write missed correlation_id=%s",
            operation,
            correlation_id,
        )
    return False


async def persist_feedback(
    *,
    correlation_id: str,
    feedback_verdict: str,
    feedback_at: datetime | None = None,
    subject_digest: str | None = None,
) -> bool:
    when = feedback_at or datetime.now(tz=UTC)
    return await _fail_open_late_write(
        operation="feedback",
        correlation_id=correlation_id,
        write=lambda repo: repo.update_feedback(
            correlation_id,
            FeedbackUpdate(feedback_verdict=feedback_verdict, feedback_at=when),
            subject_digest=subject_digest,
        ),
    )


async def persist_client_timings(
    *,
    correlation_id: str,
    ui_first_text_ms: int,
    completion_latency_ms: int,
    subject_digest: str | None = None,
) -> bool:
    return await _fail_open_late_write(
        operation="timing",
        correlation_id=correlation_id,
        write=lambda repo: repo.update_timings(
            correlation_id,
            TimingUpdate(
                ui_first_text_ms=ui_first_text_ms,
                completion_latency_ms=completion_latency_ms,
            ),
            subject_digest=subject_digest,
        ),
    )


async def persist_evaluation_join(
    *,
    correlation_id: str,
    frozen_case_id: str,
    evaluation_result: str,
    evaluation_at: datetime | None = None,
    subject_digest: str | None = None,
) -> bool:
    when = evaluation_at or datetime.now(tz=UTC)
    return await _fail_open_late_write(
        operation="evaluation",
        correlation_id=correlation_id,
        write=lambda repo: repo.update_evaluation(
            correlation_id,
            EvaluationUpdate(
                frozen_case_id=frozen_case_id,
                evaluation_result=evaluation_result,
                evaluation_at=when,
            ),
            subject_digest=subject_digest,
        ),
    )
