"""Postgres execution event store implementation using SQLAlchemy async sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_query.journaling.journal import (
    insert_conflict_digest,
    journal_json_bytes,
    journal_payload,
)
from app.business_query.seal.events.detail_evidence import _detail_evidence_row_values
from app.business_query.seal.events.digest import _bounded_digest
from app.business_query.seal.events.event import (
    BusinessQueryExecutionEvent,
    EventAccessContext,
    EventEncryptionKeyring,
    ExecutionEventConflictError,
    ExecutionEventResolutionError,
    ResolverQueryStart,
    StoredExecutionEvent,
    _decode_event,
    _encode_event,
)
from app.query_records.model import (
    BusinessQueryExecutionDetailEvidenceRow,
    BusinessQueryExecutionEventPayloadRow,
    BusinessQueryExecutionEventRow,
    BusinessQueryExecutionEventTombstoneRow,
    BusinessQueryResolverEventRow,
)


class PostgresExecutionEventStore:
    """Synchronous, append-only evidence store in the Query Record Postgres database."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        keyring: EventEncryptionKeyring | None,
    ) -> None:
        self._session = session
        self.keyring = keyring

    async def append(self, event: BusinessQueryExecutionEvent) -> str:
        record = _encode_event(event, self.keyring)
        values = {
            field: getattr(record, field)
            for field in (
                "answer_query_id",
                "project_id",
                "payload_classification",
                "metadata_json",
                "integrity_digest",
                "result_digest",
                "retention_at",
            )
        }
        stmt = pg_insert(BusinessQueryExecutionEventRow).values(**values)
        stmt = stmt.on_conflict_do_nothing(index_elements=["answer_query_id"])
        stmt = stmt.returning(BusinessQueryExecutionEventRow)
        try:
            inserted = (await self._session.execute(stmt)).scalar_one_or_none()
            if inserted is None:
                existing = (
                    await self._session.execute(
                        select(BusinessQueryExecutionEventRow)
                        .where(
                            BusinessQueryExecutionEventRow.answer_query_id == event.answer_query_id
                        )
                        .with_for_update()
                    )
                ).scalar_one()
            else:
                existing = inserted
            if existing.integrity_digest != event.integrity_digest:
                raise ExecutionEventConflictError("event idempotency digest mismatch")
            tombstone = (
                await self._session.execute(
                    select(BusinessQueryExecutionEventTombstoneRow).where(
                        BusinessQueryExecutionEventTombstoneRow.answer_query_id
                        == event.answer_query_id
                    )
                )
            ).scalar_one_or_none()
            expected_detail_rows = _detail_evidence_row_values(event)
            existing_detail_rows = (
                (
                    await self._session.execute(
                        select(BusinessQueryExecutionDetailEvidenceRow)
                        .where(
                            BusinessQueryExecutionDetailEvidenceRow.answer_query_id
                            == event.answer_query_id
                        )
                        .order_by(BusinessQueryExecutionDetailEvidenceRow.ordinal)
                    )
                )
                .scalars()
                .all()
            )
            if tombstone is None:
                if inserted is not None and existing_detail_rows:
                    raise ExecutionEventConflictError("event detail evidence mismatch")
                if inserted is None and len(existing_detail_rows) != len(expected_detail_rows):
                    raise ExecutionEventConflictError("event detail evidence mismatch")
                replay_rows = existing_detail_rows if inserted is None else []
                replay_expected = expected_detail_rows if inserted is None else []
                for expected, actual in zip(replay_expected, replay_rows, strict=True):
                    actual_values = {
                        field: getattr(actual, field)
                        for field in (
                            "answer_query_id",
                            "project_id",
                            "ordinal",
                            "family",
                            "owner_resource",
                            "owner_ref_digest",
                            "revision_digest",
                            "definition_digest",
                            "profile_digest",
                            "coverage_status",
                            "coverage_digest",
                            "provenance_digest",
                        )
                    }
                    expected_digest = expected["detail_digest"]
                    actual_digest = _bounded_digest(
                        {
                            key: value
                            for key, value in actual_values.items()
                            if key not in {"answer_query_id", "project_id", "ordinal"}
                        }
                    )
                    if (
                        actual_values.get("answer_query_id") != event.answer_query_id
                        or actual_values.get("project_id") != event.project_id
                        or actual_values.get("ordinal") != expected["ordinal"]
                        or actual.detail_digest != expected_digest
                        or actual_digest != expected_digest
                    ):
                        raise ExecutionEventConflictError("event detail evidence mismatch")
            if (
                tombstone is None
                and record.payload_ciphertext is not None
                and record.payload_nonce is not None
                and record.payload_key_version is not None
            ):
                payload_stmt = pg_insert(BusinessQueryExecutionEventPayloadRow).values(
                    answer_query_id=record.answer_query_id,
                    payload_ciphertext=record.payload_ciphertext,
                    payload_nonce=record.payload_nonce,
                    payload_key_version=record.payload_key_version,
                )
                payload_stmt = payload_stmt.on_conflict_do_nothing(
                    index_elements=["answer_query_id"]
                )
                await self._session.execute(payload_stmt)
            if tombstone is None and expected_detail_rows and not existing_detail_rows:
                detail_stmt = pg_insert(BusinessQueryExecutionDetailEvidenceRow).values(
                    expected_detail_rows
                )
                detail_stmt = detail_stmt.on_conflict_do_nothing(
                    index_elements=["answer_query_id", "ordinal"]
                )
                await self._session.execute(detail_stmt)
            await self._session.flush()
        except Exception:
            from app.telemetry.metrics import record_business_query_failure

            record_business_query_failure(kind="execution_event")
            await self._session.rollback()
            raise
        return event.answer_query_id

    async def append_resolver_started(self, event: ResolverQueryStart) -> str:
        digest = journal_payload(event, exclude={"started_at"})[0]
        event_json = journal_json_bytes(event)
        stmt = pg_insert(BusinessQueryResolverEventRow).values(
            resolver_query_id=event.resolver_query_id,
            project_id=event.project_id,
            event_kind="started",
            event_digest=digest,
            event_json=event_json,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["resolver_query_id", "event_kind"])
        stmt = stmt.returning(BusinessQueryResolverEventRow.event_digest)
        existing_stmt = select(BusinessQueryResolverEventRow.event_digest).where(
            BusinessQueryResolverEventRow.resolver_query_id == event.resolver_query_id,
            BusinessQueryResolverEventRow.event_kind == "started",
        )
        await insert_conflict_digest(
            self._session,
            stmt,
            digest=digest,
            existing_stmt=existing_stmt,
            mismatch=ExecutionEventConflictError("resolver started digest mismatch"),
        )
        return event.resolver_query_id

    async def resolve(
        self,
        answer_query_id: str,
        access: EventAccessContext,
        *,
        now: datetime | None = None,
    ) -> BusinessQueryExecutionEvent:
        row = (
            await self._session.execute(
                select(
                    BusinessQueryExecutionEventRow,
                    BusinessQueryExecutionEventPayloadRow,
                    BusinessQueryExecutionEventTombstoneRow,
                )
                .outerjoin(
                    BusinessQueryExecutionEventPayloadRow,
                    BusinessQueryExecutionEventPayloadRow.answer_query_id
                    == BusinessQueryExecutionEventRow.answer_query_id,
                )
                .outerjoin(
                    BusinessQueryExecutionEventTombstoneRow,
                    BusinessQueryExecutionEventTombstoneRow.answer_query_id
                    == BusinessQueryExecutionEventRow.answer_query_id,
                )
                .where(BusinessQueryExecutionEventRow.answer_query_id == answer_query_id)
            )
        ).one_or_none()
        if row is None:
            raise ExecutionEventResolutionError("execution event not accessible")
        event_row, payload, tombstone = row
        record = StoredExecutionEvent(
            answer_query_id=event_row.answer_query_id,
            project_id=event_row.project_id,
            payload_classification=event_row.payload_classification,
            metadata_json=event_row.metadata_json,
            integrity_digest=event_row.integrity_digest,
            result_digest=event_row.result_digest,
            payload_ciphertext=payload.payload_ciphertext if payload is not None else None,
            payload_nonce=payload.payload_nonce if payload is not None else None,
            payload_key_version=payload.payload_key_version if payload is not None else None,
            retention_at=event_row.retention_at,
            tombstoned_at=tombstone.tombstoned_at if tombstone is not None else None,
            tombstone_reason_digest=tombstone.reason_digest if tombstone is not None else None,
        )
        return _decode_event(
            record,
            keyring=self.keyring,
            access=access,
            now=now or datetime.now(tz=UTC),
        )

    async def tombstone(
        self,
        answer_query_id: str,
        access: EventAccessContext,
        *,
        reason_digest: str,
        tombstoned_at: datetime,
    ) -> None:
        if access.purpose != "retention":
            raise ExecutionEventResolutionError("execution event not accessible")
        event = (
            await self._session.execute(
                select(BusinessQueryExecutionEventRow)
                .where(BusinessQueryExecutionEventRow.answer_query_id == answer_query_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if event is None or event.project_id != access.project_id:
            raise ExecutionEventResolutionError("execution event not accessible")
        stmt = pg_insert(BusinessQueryExecutionEventTombstoneRow).values(
            answer_query_id=answer_query_id,
            project_id=access.project_id,
            reason_digest=reason_digest,
            tombstoned_at=tombstoned_at,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["answer_query_id"])
        await self._session.execute(stmt)
        await self._session.execute(
            delete(BusinessQueryExecutionEventPayloadRow).where(
                BusinessQueryExecutionEventPayloadRow.answer_query_id == answer_query_id
            )
        )
        await self._session.execute(
            delete(BusinessQueryExecutionDetailEvidenceRow).where(
                BusinessQueryExecutionDetailEvidenceRow.answer_query_id == answer_query_id
            )
        )
        await self._session.commit()
        tombstone = (
            await self._session.execute(
                select(BusinessQueryExecutionEventTombstoneRow).where(
                    BusinessQueryExecutionEventTombstoneRow.answer_query_id == answer_query_id
                )
            )
        ).scalar_one()
        if tombstone.reason_digest != reason_digest:
            raise ExecutionEventConflictError("event tombstone digest mismatch")
