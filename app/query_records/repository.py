"""Query Record persistence — insert, feedback update, retention scaffolding."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.query_records.model import QueryRecordRow
from app.query_records.types import (
    EvaluationUpdate,
    FeedbackUpdate,
    QueryRecordData,
    TimingUpdate,
    row_to_dict,
)


class FeedbackTargetMissingError(LookupError):
    """Late feedback update missed — no row for the correlation id."""


class QueryRecordInsertVisibilityError(RuntimeError):
    """Insert flushed but the row is not readable — do not invent persisted state."""


_TERMINAL_RECONCILE_FIELDS = frozenset(
    {
        "terminal_outcome",
        "ui_first_text_ms",
        "completion_latency_ms",
        "model",
        "provider",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cost_status",
        "resolver_query_id",
        "resolver_disposition",
        "row_count",
        "bq_trace_json",
        "stable_error_code",
        "timeout",
        "cancelled",
    }
)

# The row key and its creation stamp identify the row a reconcile targets, so a
# second delivery must never carry either one.
_RECONCILE_NEVER_FIELDS = frozenset({"correlation_id", "created_at"})


def _reconcilable_values(payload: Mapping[str, Any], existing: Mapping[str, Any]) -> dict[str, Any]:
    """Columns a second delivery for one correlation may write.

    Two different second writes reach this seam. A duplicate terminal repeats a
    turn that already reported, and may refresh only the terminal/timing/token
    fields. An execution reservation instead opens the row before the route,
    cost, or reason code exist, which makes the real terminal write the first
    writer for every column the reservation left empty. Filling an empty column
    does not alter a recorded value, so one rule keeps both safe: reconcile the
    terminal field set, and complete anything still unset.
    """
    return {
        key: value
        for key, value in payload.items()
        if value is not None
        and key not in _RECONCILE_NEVER_FIELDS
        and (key in _TERMINAL_RECONCILE_FIELDS or existing.get(key) is None)
    }


class QueryRecordRepository:
    def __init__(self, session: AsyncSession, *, table_name: str = "query_records") -> None:
        self._session = session
        self._table_name = table_name

    async def insert(self, record: QueryRecordData) -> dict:
        """Insert one row.

        A second delivery for a ``correlation_id`` refreshes the terminal field
        set and completes any column still unset; a value already recorded stays
        first-write immutable. Never returns an unverified in-memory dump as if
        it were persisted.
        """
        payload = record.model_dump()
        if payload.get("created_at") is None:
            payload.pop("created_at", None)
        stmt = pg_insert(QueryRecordRow).values(**payload)
        stmt = stmt.on_conflict_do_nothing(index_elements=["correlation_id"])
        result = await self._session.execute(stmt)
        await self._session.flush()
        self._session.expire_all()

        existing = await self.get_by_correlation_id(record.correlation_id)
        if existing is not None:
            # An execution reservation, the synchronous BQ projection, and the
            # ordinary terminal hook all reach this correlation legitimately.
            if result.rowcount == 0:
                values = _reconcilable_values(payload, row_to_dict(existing))
                if values:
                    await self._session.execute(
                        update(QueryRecordRow)
                        .where(QueryRecordRow.correlation_id == record.correlation_id)
                        .values(**values)
                    )
                    await self._session.flush()
                    self._session.expire_all()
                    existing = await self.get_by_correlation_id(record.correlation_id)
            return row_to_dict(existing)

        if result.rowcount == 0:
            raise QueryRecordInsertVisibilityError(
                f"duplicate insert reported but row missing correlation_id={record.correlation_id}"
            )
        raise QueryRecordInsertVisibilityError(
            f"insert flushed but row not readable correlation_id={record.correlation_id}"
        )

    async def reserve_execution(
        self,
        *,
        correlation_id: str,
        project_id: str = "default",
        environment: str = "development",
        question: str | None = None,
        requested_route: str | None = None,
    ) -> dict:
        """Reserve execution identity before claiming continuation.

        Writes pending row if none exists; if existing row exists, returns it.
        A reservation opens before routing is decided, so it leaves
        ``requested_route`` unset unless the caller already knows the route.
        The terminal write is then the first writer for it.
        """
        stmt = pg_insert(QueryRecordRow).values(
            correlation_id=correlation_id,
            project_id=project_id,
            environment=environment,
            raw_question=question,
            requested_route=requested_route,
            terminal_outcome="pending",
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["correlation_id"])
        await self._session.execute(stmt)
        await self._session.flush()
        self._session.expire_all()

        existing = await self.get_by_correlation_id(correlation_id)
        if existing is None:
            raise QueryRecordInsertVisibilityError(
                f"reserve_execution flushed but row not readable correlation_id={correlation_id}"
            )
        return row_to_dict(existing)

    async def get_by_correlation_id(self, correlation_id: str) -> QueryRecordRow | None:
        stmt = select(QueryRecordRow).where(QueryRecordRow.correlation_id == correlation_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_by_correlation_id(self, correlation_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(QueryRecordRow)
            .where(QueryRecordRow.correlation_id == correlation_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def _apply_late_update(
        self,
        *,
        correlation_id: str,
        values: Mapping[str, Any],
        mutable_keys: frozenset[str],
        mutation_label: str,
        subject_digest: str | None = None,
    ) -> dict:
        """Shared late-write path — optional subject_digest gate, then immutability check."""
        if subject_digest is not None:
            before_row = await self.get_by_correlation_id(correlation_id)
            if before_row is None or before_row.subject_digest != subject_digest:
                raise FeedbackTargetMissingError(correlation_id)
            before = row_to_dict(before_row)
            stmt = (
                update(QueryRecordRow)
                .where(QueryRecordRow.correlation_id == correlation_id)
                .where(QueryRecordRow.subject_digest == subject_digest)
                .values(**values)
            )
            result = await self._session.execute(stmt)
            await self._session.commit()
            if result.rowcount == 0:
                raise FeedbackTargetMissingError(correlation_id)
        else:
            existing = await self.get_by_correlation_id(correlation_id)
            if existing is None:
                raise FeedbackTargetMissingError(correlation_id)
            before = row_to_dict(existing)
            stmt = (
                update(QueryRecordRow)
                .where(QueryRecordRow.correlation_id == correlation_id)
                .values(**values)
            )
            await self._session.execute(stmt)
            await self._session.commit()

        self._session.expire_all()
        after_row = await self.get_by_correlation_id(correlation_id)
        if after_row is None:
            raise FeedbackTargetMissingError(correlation_id)
        after = row_to_dict(after_row)

        for key, value in before.items():
            if key in mutable_keys:
                continue
            if after[key] != value:
                raise RuntimeError(f"{mutation_label} mutated column {key}")

        return after

    async def update_feedback(
        self,
        correlation_id: str,
        feedback: FeedbackUpdate,
        *,
        subject_digest: str | None = None,
    ) -> dict:
        """Late write — feedback only; never creates a row."""
        return await self._apply_late_update(
            correlation_id=correlation_id,
            values={
                "feedback_verdict": feedback.feedback_verdict,
                "feedback_at": feedback.feedback_at,
            },
            mutable_keys=frozenset({"feedback_verdict", "feedback_at"}),
            mutation_label="late write",
            subject_digest=subject_digest,
        )

    async def update_timings(
        self,
        correlation_id: str,
        timings: TimingUpdate,
        *,
        subject_digest: str | None = None,
    ) -> dict:
        return await self._apply_late_update(
            correlation_id=correlation_id,
            values={
                "ui_first_text_ms": timings.ui_first_text_ms,
                "completion_latency_ms": timings.completion_latency_ms,
            },
            mutable_keys=frozenset({"ui_first_text_ms", "completion_latency_ms"}),
            mutation_label="timing late write",
            subject_digest=subject_digest,
        )

    async def update_evaluation(
        self,
        correlation_id: str,
        evaluation: EvaluationUpdate,
        *,
        subject_digest: str | None = None,
    ) -> dict:
        return await self._apply_late_update(
            correlation_id=correlation_id,
            values={
                "frozen_case_id": evaluation.frozen_case_id,
                "evaluation_result": evaluation.evaluation_result,
                "evaluation_at": evaluation.evaluation_at,
            },
            mutable_keys=frozenset({"frozen_case_id", "evaluation_result", "evaluation_at"}),
            mutation_label="evaluation late write",
            subject_digest=subject_digest,
        )

    async def purge_before(self, *, project_id: str, cutoff: datetime) -> int:
        """Retention scaffolding — caller supplies cutoff (window BLOCKED-ON-OWNER)."""
        stmt = (
            delete(QueryRecordRow)
            .where(QueryRecordRow.project_id == project_id)
            .where(QueryRecordRow.retention_at.is_not(None))
            .where(QueryRecordRow.retention_at < cutoff)
        )
        result = await self._session.execute(stmt)
        await self._session.commit()
        return int(result.rowcount or 0)

    async def delete_by_subject_digest(self, *, subject_digest: str) -> int:
        """Erasure scaffolding — table scope only; backups/vendor stores out of scope."""
        stmt = delete(QueryRecordRow).where(QueryRecordRow.subject_digest == subject_digest)
        result = await self._session.execute(stmt)
        await self._session.commit()
        return int(result.rowcount or 0)

    async def explain_project_filter(self, *, project_id: str) -> str:
        """Return EXPLAIN output for a project-filtered listing."""
        sql = text(
            f"EXPLAIN SELECT * FROM {self._table_name} "
            "WHERE project_id = :pid ORDER BY created_at DESC LIMIT 10"
        )
        result = await self._session.execute(sql, {"pid": project_id})
        lines = [row[0] for row in result.fetchall()]
        return "\n".join(lines)

    async def insert_into_unavailable_table(self, record: QueryRecordData) -> None:
        """Test seam — write to a non-existent table."""
        sql = text(f"INSERT INTO {self._table_name}_missing (correlation_id) VALUES (:cid)")
        try:
            await self._session.execute(sql, {"cid": record.correlation_id})
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
