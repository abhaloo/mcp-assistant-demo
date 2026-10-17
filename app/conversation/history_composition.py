"""Composition root for the durable thread history (ADR 0085)."""

from __future__ import annotations

from datetime import timedelta

from app.config import settings
from app.conversation.durable_history import DurableHistorySource
from app.query_records.dispatch import query_record_store_configured
from app.query_records.thread_history import read_thread_rows

# One indexed read (ix_query_records_thread_id) before the turn runs; a store
# slower than this leaves the turn without rebuilt memory instead of holding it.
DURABLE_HISTORY_READ_TIMEOUT_SECONDS = 2.0


def build_durable_history() -> DurableHistorySource | None:
    """The thread history kept in the Query Records, or None with no store."""
    if not query_record_store_configured():
        return None
    return DurableHistorySource(
        read_rows=read_thread_rows,
        # ADR 0028 invariant 5: a thread's memory lives 4 hours from its start.
        max_thread_age=timedelta(seconds=settings.conversation_absolute_ttl_seconds),
        read_timeout_seconds=DURABLE_HISTORY_READ_TIMEOUT_SECONDS,
    )
