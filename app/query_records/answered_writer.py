"""State implementation of the synchronous Answered Query Record write port."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.query_records.failure_counter import increment_write_failure
from app.query_records.repository import QueryRecordRepository
from app.query_records.types import QueryRecordData


class PostgresAnsweredQueryRecordWriter:
    """Keep repository and failure-counter details behind the business port."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = QueryRecordRepository(session)

    async def bind_sql_receipt(self, correlation_id: str, answer_query_id: str) -> None:
        """Confirm SQL ledger rows for this Answer Query ID before Answered is exposed.

        Execute stamps ``receipt_query_id`` per plan. Other plans on the same
        correlation keep their own IDs. Bind only unbound rows when this ID
        is not yet present.
        """
        from app.telemetry.invocation_ledger import SqlExecutionRow, flush_ledger_writes

        await flush_ledger_writes()
        already = (
            await self._session.execute(
                select(func.count())
                .select_from(SqlExecutionRow)
                .where(
                    SqlExecutionRow.correlation_id == correlation_id,
                    SqlExecutionRow.receipt_query_id == answer_query_id,
                )
            )
        ).scalar_one()
        if already:
            return

        await self._session.execute(
            update(SqlExecutionRow)
            .where(
                SqlExecutionRow.correlation_id == correlation_id,
                SqlExecutionRow.receipt_query_id.is_(None),
            )
            .values(receipt_query_id=answer_query_id)
        )
        bound = (
            await self._session.execute(
                select(func.count())
                .select_from(SqlExecutionRow)
                .where(
                    SqlExecutionRow.correlation_id == correlation_id,
                    SqlExecutionRow.receipt_query_id == answer_query_id,
                )
            )
        ).scalar_one()
        if not bound:
            raise RuntimeError("SQL execution evidence is unavailable for Answered result")

    async def write(self, record: QueryRecordData) -> None:
        await self._repository.insert(record)

    def record_failure(self) -> None:
        increment_write_failure()
