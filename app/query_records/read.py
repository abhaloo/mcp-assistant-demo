"""Query Record read API — sorting and filtering."""

from __future__ import annotations

import json

from sqlalchemy import asc, desc, nullsfirst, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.query_records.model import SORTABLE_COLUMNS, QueryRecordRow
from app.query_records.types import QueryListRequest, row_to_dict


class UnknownSortColumnError(ValueError):
    pass


def _order_clause(column_name: str, *, direction: str, nulls: str):
    column = getattr(QueryRecordRow, column_name)
    ordering = asc(column) if direction == "asc" else desc(column)
    if nulls == "first":
        return nullsfirst(ordering)
    return nullslast(ordering)


class QueryRecordReadService:
    """Repository-enforced project filter; explicit NULL placement on every sort."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_records(self, request: QueryListRequest) -> list[dict]:
        if request.sort.column not in SORTABLE_COLUMNS:
            raise UnknownSortColumnError(request.sort.column)

        stmt = select(QueryRecordRow).where(QueryRecordRow.project_id == request.filters.project_id)

        filters = request.filters
        if filters.billing_commit is not None:
            stmt = stmt.where(QueryRecordRow.billing_commit == filters.billing_commit)
        if filters.rag_commit is not None:
            stmt = stmt.where(QueryRecordRow.rag_commit == filters.rag_commit)
        if filters.manifest_hash is not None:
            stmt = stmt.where(QueryRecordRow.manifest_hash == filters.manifest_hash)
        if filters.conversation_mode is not None:
            stmt = stmt.where(QueryRecordRow.conversation_mode == filters.conversation_mode)
        if filters.feedback_verdict is not None:
            stmt = stmt.where(QueryRecordRow.feedback_verdict == filters.feedback_verdict)
        if filters.prompt_version is not None:
            expected = json.dumps({"router": filters.prompt_version})
            stmt = stmt.where(QueryRecordRow.prompt_versions == expected)

        stmt = stmt.order_by(
            _order_clause(
                request.sort.column,
                direction=request.sort.direction,
                nulls=request.sort.nulls,
            )
        ).limit(request.limit)

        result = await self._session.execute(stmt)
        return [row_to_dict(row) for row in result.scalars().all()]
