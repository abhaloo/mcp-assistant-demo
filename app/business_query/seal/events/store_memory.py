"""In-memory execution event store for test suites and isolation."""

from __future__ import annotations

import base64
from asyncio import Lock
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.business_query.journaling.journal import journal_payload
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


class InMemoryExecutionEventStore:
    """Concurrency-safe deterministic adapter for event contract tests."""

    def __init__(self, *, keyring: EventEncryptionKeyring | None) -> None:
        self.keyring = keyring
        self._records: dict[str, StoredExecutionEvent] = {}
        self._resolver_started: dict[str, tuple[str, ResolverQueryStart]] = {}
        self._lock = Lock()

    @property
    def count(self) -> int:
        return len(self._records)

    @property
    def resolver_start_count(self) -> int:
        return len(self._resolver_started)

    async def append(self, event: BusinessQueryExecutionEvent) -> str:
        record = _encode_event(event, self.keyring)
        async with self._lock:
            existing = self._records.get(event.answer_query_id)
            if existing is not None:
                if existing.integrity_digest != event.integrity_digest:
                    raise ExecutionEventConflictError("event idempotency digest mismatch")
                return event.answer_query_id
            self._records[event.answer_query_id] = record
        return event.answer_query_id

    async def append_resolver_started(self, event: ResolverQueryStart) -> str:
        digest, _payload = journal_payload(event, exclude={"started_at"})
        async with self._lock:
            existing = self._resolver_started.get(event.resolver_query_id)
            if existing is not None:
                if existing[0] != digest:
                    raise ExecutionEventConflictError("resolver started digest mismatch")
                return event.resolver_query_id
            self._resolver_started[event.resolver_query_id] = (digest, event)
        return event.resolver_query_id

    async def resolve(
        self,
        answer_query_id: str,
        access: EventAccessContext,
        *,
        now: datetime | None = None,
    ) -> BusinessQueryExecutionEvent:
        async with self._lock:
            record = self._records.get(answer_query_id)
            if record is None:
                raise ExecutionEventResolutionError("execution event not accessible")
            snapshot = replace(record)
        decoded = _decode_event(
            snapshot,
            keyring=self.keyring,
            access=access,
            now=now or datetime.now(tz=UTC),
        )
        async with self._lock:
            current = self._records.get(answer_query_id)
            if current is None:
                raise ExecutionEventResolutionError("execution event not accessible")
            if current.tombstoned_at is not None:
                raise ExecutionEventResolutionError("execution event tombstoned")
        return decoded

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
        async with self._lock:
            record = self._records.get(answer_query_id)
            if record is None or record.project_id != access.project_id:
                raise ExecutionEventResolutionError("execution event not accessible")
            if record.tombstoned_at is not None:
                if record.tombstone_reason_digest != reason_digest:
                    raise ExecutionEventConflictError("event tombstone digest mismatch")
                return
            self._records[answer_query_id] = replace(
                record,
                tombstoned_at=tombstoned_at,
                tombstone_reason_digest=reason_digest,
                payload_ciphertext=None,
                payload_nonce=None,
            )

    def stored_record(self, answer_query_id: str) -> StoredExecutionEvent:
        return self._records[answer_query_id]

    def artifact_record(self, answer_query_id: str) -> dict[str, Any]:
        """Mirror committed evidence without decrypting its result payload."""
        record = self._records[answer_query_id]
        return {
            "answer_query_id": record.answer_query_id,
            "project_id": record.project_id,
            "payload_classification": record.payload_classification,
            "metadata_json_b64": base64.b64encode(record.metadata_json).decode("ascii"),
            "integrity_digest": record.integrity_digest,
            "result_digest": record.result_digest,
            "payload_ciphertext_b64": (
                base64.b64encode(record.payload_ciphertext).decode("ascii")
                if record.payload_ciphertext is not None
                else None
            ),
            "payload_nonce_b64": (
                base64.b64encode(record.payload_nonce).decode("ascii")
                if record.payload_nonce is not None
                else None
            ),
            "payload_key_version": record.payload_key_version,
            "retention_at": record.retention_at.isoformat(),
            "tombstoned_at": (
                record.tombstoned_at.isoformat() if record.tombstoned_at is not None else None
            ),
            "tombstone_reason_digest": record.tombstone_reason_digest,
        }

    def tamper_ciphertext_for_tests(self, answer_query_id: str) -> None:
        record = self._records[answer_query_id]
        if record.payload_ciphertext is None:
            raise ValueError("event has no ciphertext")
        tampered = bytes([record.payload_ciphertext[0] ^ 1]) + record.payload_ciphertext[1:]
        self._records[answer_query_id] = replace(record, payload_ciphertext=tampered)
