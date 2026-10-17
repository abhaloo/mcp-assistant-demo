"""Read one person's kept exchanges of a thread from the Query Records (ADR 0085)."""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import aliased

from app.auth import Principal
from app.conversation.durable_history import ThreadRow
from app.db.postgres import session_scope
from app.query_records.content import subject_digest
from app.query_records.model import QueryRecordRow


def _owned_by(row: type[QueryRecordRow], *, thread_id: str, principal: Principal) -> tuple:
    """The rows of this thread that this person and entity own.

    The browser names the thread, so the read also names the person and the
    entity that own the rows.
    """
    owner_entity = (
        row.entity_id == str(principal.entity_id)
        if principal.entity_id is not None
        else row.entity_id.is_(None)
    )
    return (
        row.thread_id == thread_id,
        row.subject_digest == subject_digest(str(principal.user_id)),
        owner_entity,
    )


def thread_rows_statement(*, thread_id: str, principal: Principal, limit: int) -> Select:
    """The newest ``limit`` answered rows of one person's thread, newest first.

    Each row carries the time of the thread's first row of any outcome, so the
    caller can tell how old the thread is.
    """
    first = aliased(QueryRecordRow)
    started_at = (
        select(func.min(first.created_at))
        .where(*_owned_by(first, thread_id=thread_id, principal=principal))
        .scalar_subquery()
    )
    return (
        select(
            QueryRecordRow.exchange_id,
            QueryRecordRow.operation,
            QueryRecordRow.raw_question,
            QueryRecordRow.redacted_question,
            QueryRecordRow.answer_text,
            started_at.label("thread_started_at"),
        )
        .where(
            *_owned_by(QueryRecordRow, thread_id=thread_id, principal=principal),
            QueryRecordRow.exchange_id.is_not(None),
            QueryRecordRow.answer_text.is_not(None),
        )
        .order_by(QueryRecordRow.created_at.desc(), QueryRecordRow.id.desc())
        .limit(limit)
    )


async def read_thread_rows(*, thread_id: str, principal: Principal, limit: int) -> list[ThreadRow]:
    """One person's kept exchanges of a thread, oldest first."""
    statement = thread_rows_statement(thread_id=thread_id, principal=principal, limit=limit)
    async with session_scope() as session:
        rows = (await session.execute(statement)).all()
    return [
        ThreadRow(
            exchange_id=row.exchange_id,
            operation=row.operation,
            # A clarification reply's request names its choice, not a question,
            # so its row keeps only the redacted reply text.
            question=row.raw_question or row.redacted_question or "",
            answer=row.answer_text,
            started_at=row.thread_started_at,
        )
        for row in reversed(rows)
    ]
