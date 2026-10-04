"""Query Record persistence — insert, feedback update, retention scaffolding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, delete, func, select, text, update
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


@dataclass(frozen=True)
class PeriodSpend:
    priced_usd: Decimal
    unpriced_input_tokens: int
    unpriced_output_tokens: int
    rows: int


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
        "estimated_usd",
        "cost_status",
        "price_table_version",
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
    values = {
        key: value
        for key, value in payload.items()
        if value is not None
        and key not in _RECONCILE_NEVER_FIELDS
        and (key in _TERMINAL_RECONCILE_FIELDS or existing.get(key) is None)
    }
    # A terminal that could not price the turn withdraws the price an earlier
    # seal wrote for one call of it; the row never pairs a price with "unknown".
    if payload.get("cost_status") == "unknown":
        values["estimated_usd"] = None
    # The provider belongs to the call that named the model, so it moves only
    # with a model; a write that names no model leaves the recorded pair alone.
    if payload.get("model") is not None:
        values["provider"] = payload.get("provider")
    return values


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

    async def reserve_execution(  # noqa: PLR0913 - the row's owner stamps ride with its reservation
        self,
        *,
        correlation_id: str,
        project_id: str = "default",
        environment: str = "development",
        question: str | None = None,
        requested_route: str | None = None,
        thread_id: str | None = None,
        subject_digest: str | None = None,
        entity_id: str | None = None,
    ) -> dict:
        """Reserve execution identity before claiming continuation.

        Writes pending row if none exists; if existing row exists, returns it.
        A reservation opens before routing is decided, so it leaves
        ``requested_route`` unset unless the caller already knows the route.
        The terminal write is then the first writer for it. The row names its
        thread, person and entity from the start, so a turn that never reaches
        its terminal write is still found in its thread.
        """
        stmt = pg_insert(QueryRecordRow).values(
            correlation_id=correlation_id,
            project_id=project_id,
            environment=environment,
            raw_question=question or None,
            requested_route=requested_route,
            thread_id=thread_id,
            subject_digest=subject_digest,
            entity_id=entity_id,
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

    async def spend_in_period(
        self,
        *,
        entity_id: str,
        period_start: datetime,
        period_end: datetime,
        project_id: str,
    ) -> PeriodSpend:
        """Sum period spend and count unpriced tokens for an entity within one
        project; the caller names the project the writer stamps on every row."""
        stmt = (
            select(
                func.coalesce(func.sum(QueryRecordRow.estimated_usd), Decimal("0")),
                func.coalesce(
                    func.sum(
                        case(
                            (QueryRecordRow.estimated_usd.is_(None), QueryRecordRow.input_tokens),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (QueryRecordRow.estimated_usd.is_(None), QueryRecordRow.output_tokens),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.count(),
            )
            .where(QueryRecordRow.entity_id == entity_id)
            .where(QueryRecordRow.created_at >= period_start)
            .where(QueryRecordRow.created_at < period_end)
            .where(QueryRecordRow.project_id == project_id)
        )
        result = await self._session.execute(stmt)
        row = result.one()
        priced_usd, unpriced_in, unpriced_out, count = row
        return PeriodSpend(
            priced_usd=Decimal(str(priced_usd)),
            unpriced_input_tokens=int(unpriced_in or 0),
            unpriced_output_tokens=int(unpriced_out or 0),
            rows=int(count or 0),
        )
