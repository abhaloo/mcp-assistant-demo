"""Fail-open write seam for Query Record."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.query_records.failure_counter import increment_write_failure
from app.query_records.types import QueryRecordData

logger = logging.getLogger(__name__)


async def fail_open_write(
    write_fn: Callable[[QueryRecordData], Awaitable[None]],
    record: QueryRecordData,
) -> None:
    """Attempt a Query Record write without failing the caller's answer path."""
    try:
        await write_fn(record)
    except Exception:
        increment_write_failure()
        logger.warning("Query Record write failed (counted, fail-open)", exc_info=True)
