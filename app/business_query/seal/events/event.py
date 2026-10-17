"""Execution event models, encoding/decoding, and integrity checks."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from cryptography.exceptions import InvalidTag
from pydantic import BaseModel, ConfigDict, Field

from app.business_query.outcomes import DECIMAL_VALUE_KINDS
from app.business_query.seal.events.detail_evidence import (
    _EMPTY_DETAIL_EVIDENCE_DIGEST,
    _MAX_DETAIL_EVIDENCE_ITEMS,
    DetailEvidence,
    detail_evidence_digest,
)
from app.business_query.seal.events.digest import _aad, _canonical_json, _sha256
from app.crypto.aead import decrypt_bytes, encrypt_bytes
from app.crypto.event_keyring import EventEncryptionKeyring

if TYPE_CHECKING:
    from app.config import Settings


class EventPayloadMode(StrEnum):
    ENCRYPTED = "encrypted"
    DIGEST_ONLY = "digest_only"


EventPurpose = Literal["evaluation", "answer_release", "retention", "artifact_export"]
PayloadClassification = Literal["evaluation", "answer_release"]


@dataclass(frozen=True)
class EventAccessContext:
    project_id: str
    purpose: EventPurpose

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("event access project is required")
        if self.purpose not in {"evaluation", "answer_release", "retention", "artifact_export"}:
            raise ValueError("event access purpose is not allowed")


def build_event_access_context(purpose: EventPurpose, settings: Settings) -> EventAccessContext:
    """Build trusted access from server configuration, never request data."""
    return EventAccessContext(project_id=settings.query_record_project_id, purpose=purpose)


class ResultMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    value_kind: Literal[
        "null",
        "boolean",
        "integer",
        "decimal",
        "percent",
        "currency",
        "string",
        "date",
        "datetime",
    ]
    nullable: bool = False


class ExecutionEventError(RuntimeError):
    pass


class ExecutionEventConflictError(ExecutionEventError):
    pass


class ExecutionEventResolutionError(ExecutionEventError):
    pass


def _validate_result_rows(
    members: tuple[ResultMember, ...], rows: tuple[dict[str, Any], ...]
) -> None:
    names = [member.name for member in members]
    if len(names) != len(set(names)):
        raise ExecutionEventConflictError("event member schema contains duplicates")
    expected = set(names)
    if any(set(row) != expected for row in rows):
        raise ExecutionEventConflictError("event row does not match member schema")
    by_name = {member.name: member for member in members}
    for row in rows:
        for name, value in row.items():
            member = by_name[name]
            if value is None:
                if not member.nullable:
                    raise ExecutionEventConflictError("event row violates member nullability")
                continue
            kind = member.value_kind
            valid = (
                (kind == "boolean" and isinstance(value, bool))
                or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
                or (kind in DECIMAL_VALUE_KINDS and isinstance(value, Decimal))
                or (kind == "string" and isinstance(value, str))
                or (kind == "date" and isinstance(value, date) and not isinstance(value, datetime))
                or (kind == "datetime" and isinstance(value, datetime))
            )
            if kind == "null":
                valid = False
            if not valid:
                raise ExecutionEventConflictError("event row violates member value kind")


def _restore_typed_rows(
    members: tuple[ResultMember, ...], rows: list[dict[str, Any]]
) -> tuple[dict[str, Any], ...]:
    kinds = {member.name: member.value_kind for member in members}
    restored: list[dict[str, Any]] = []
    for row in rows:
        typed: dict[str, Any] = {}
        for name, value in row.items():
            kind = kinds.get(name)
            if value is None:
                typed[name] = None
            elif kind in DECIMAL_VALUE_KINDS:
                typed[name] = Decimal(str(value))
            elif kind == "date":
                typed[name] = date.fromisoformat(str(value))
            elif kind == "datetime":
                typed[name] = datetime.fromisoformat(str(value))
            else:
                typed[name] = value
        restored.append(typed)
    return tuple(restored)


class BusinessQueryExecutionEvent(BaseModel):
    """Immutable executor evidence. Result rows are deliberately absent from repr."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    answer_query_id: str = Field(min_length=1, max_length=128)
    root_answer_query_id: str | None = Field(default=None, max_length=128)
    project_id: str = Field(min_length=1, max_length=64)
    adapter: str = Field(min_length=1, max_length=64)
    backend: str = Field(min_length=1, max_length=64)
    plan_fingerprint: str = Field(min_length=64, max_length=64)
    compiled_query_digest: str = Field(min_length=64, max_length=64)
    parameter_scope_digest: str = Field(min_length=64, max_length=64)
    bundle_hash: str = Field(min_length=64, max_length=64)
    manifest_hash: str = Field(min_length=64, max_length=64)
    result_members: tuple[ResultMember, ...]
    result_rows: tuple[dict[str, Any], ...] = Field(repr=False)
    result_digest: str = Field(min_length=64, max_length=64)
    detail_evidence: tuple[DetailEvidence, ...] = Field(
        default=(), max_length=_MAX_DETAIL_EVIDENCE_ITEMS
    )
    detail_evidence_digest: str = Field(
        default=_EMPTY_DETAIL_EVIDENCE_DIGEST, min_length=64, max_length=64
    )
    returned_row_count: int = Field(ge=0)
    total_row_count: int = Field(ge=0)
    truncated: bool
    database_identity: str = Field(min_length=1, max_length=256)
    started_at: datetime
    finished_at: datetime
    retention_at: datetime
    payload_mode: EventPayloadMode
    payload_classification: PayloadClassification = "evaluation"
    route: str | None = None
    provider: str | None = None
    deployment: str | None = None
    output_mode: str | None = None
    effort: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    correlation_id: str | None = None
    case_id: str | None = None
    repeat_index: int | None = Field(default=None, ge=0)
    planner_attempt_id: str | None = None
    resolver_query_id: str | None = None
    classifier_ms: float | None = None
    planner_ms: float | None = None
    sql_ms: float | None = None
    detail_read_ms: float | None = None
    rich_render_ms: float | None = None
    finalization_ms: float | None = None
    first_progress_ms: float | None = None
    total_ms: float | None = None
    result_completeness: Literal["complete", "partial"] = "complete"
    coverage_status: Literal["verified_complete", "incomplete", "unknown"] = "verified_complete"
    failed_detail_families: tuple[str, ...] = ()
    policy_hash: str | None = None
    record_referent_digest: str | None = None
    terminal_reason: str | None = None
    integrity_digest: str = Field(min_length=64, max_length=64)

    @classmethod
    def create(cls, **values: Any) -> BusinessQueryExecutionEvent:
        result_rows = tuple(values["result_rows"])
        values["result_rows"] = result_rows
        members = tuple(
            member if isinstance(member, ResultMember) else ResultMember(**member)
            for member in values["result_members"]
        )
        values["result_members"] = members
        detail_evidence = tuple(
            detail if isinstance(detail, DetailEvidence) else DetailEvidence(**detail)
            for detail in values.get("detail_evidence", ())
        )
        values["detail_evidence"] = detail_evidence
        values["detail_evidence_digest"] = detail_evidence_digest(detail_evidence)
        _validate_result_rows(members, result_rows)
        values["result_digest"] = _sha256(_canonical_json(result_rows))
        values["integrity_digest"] = "0" * 64
        draft = cls(**values)
        return draft.model_copy(update={"integrity_digest": draft.recomputed_integrity_digest()})

    def _metadata(self) -> dict[str, Any]:
        metadata = self.model_dump(
            mode="json",
            exclude={"result_rows", "integrity_digest"},
        )
        if not self.detail_evidence:
            metadata.pop("detail_evidence", None)
            metadata.pop("detail_evidence_digest", None)
        return metadata

    def recomputed_integrity_digest(self) -> str:
        return _sha256(_canonical_json(self._metadata()))

    def validate_integrity(self) -> None:
        _validate_result_rows(self.result_members, self.result_rows)
        if _sha256(_canonical_json(self.result_rows)) != self.result_digest:
            raise ExecutionEventConflictError("event result digest mismatch")
        if detail_evidence_digest(self.detail_evidence) != self.detail_evidence_digest:
            raise ExecutionEventConflictError("event detail evidence digest mismatch")
        if self.recomputed_integrity_digest() != self.integrity_digest:
            raise ExecutionEventConflictError("event integrity digest mismatch")
        if self.returned_row_count != len(self.result_rows):
            raise ExecutionEventConflictError("event returned-row digest mismatch")
        if self.total_row_count < self.returned_row_count:
            raise ExecutionEventConflictError("event total-row digest mismatch")
        if self.finished_at < self.started_at:
            raise ExecutionEventConflictError("event timing digest mismatch")
        if self.retention_at <= self.finished_at:
            raise ExecutionEventConflictError("event retention deadline is not in the future")


@dataclass(frozen=True, repr=False)
class StoredExecutionEvent:
    answer_query_id: str
    project_id: str
    payload_classification: PayloadClassification
    metadata_json: bytes
    integrity_digest: str
    result_digest: str
    payload_ciphertext: bytes | None
    payload_nonce: bytes | None
    payload_key_version: str | None
    retention_at: datetime
    tombstoned_at: datetime | None = None
    tombstone_reason_digest: str | None = None

    def __repr__(self) -> str:
        return (
            "StoredExecutionEvent("
            f"answer_query_id={self.answer_query_id!r}, project_id={self.project_id!r}, "
            f"integrity_digest={self.integrity_digest!r}, "
            f"payload_key_version={self.payload_key_version!r}, "
            f"tombstoned={self.tombstoned_at is not None})"
        )


class ResolverQueryStart(BaseModel):
    """Durable Resolver Query started event (AC6). Integrity is the journal digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resolver_query_id: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=64)
    value_type: Literal["customer", "product"]
    resolver_version: str = Field(min_length=1, max_length=64)
    started_at: datetime


def _purpose_allowed(classification: PayloadClassification, purpose: EventPurpose) -> bool:
    if classification == "answer_release":
        return purpose in {"answer_release", "evaluation"}
    return purpose == "evaluation"


def _validate_record_binding(
    record: StoredExecutionEvent, event: BusinessQueryExecutionEvent
) -> None:
    if (
        record.answer_query_id != event.answer_query_id
        or record.project_id != event.project_id
        or record.payload_classification != event.payload_classification
        or record.result_digest != event.result_digest
        or record.integrity_digest != event.integrity_digest
        or record.retention_at != event.retention_at
    ):
        raise ExecutionEventResolutionError("execution event integrity check failed")


def _encode_event(
    event: BusinessQueryExecutionEvent,
    keyring: EventEncryptionKeyring | None,
) -> StoredExecutionEvent:
    event.validate_integrity()
    metadata_json = _canonical_json(event._metadata())
    record = StoredExecutionEvent(
        answer_query_id=event.answer_query_id,
        project_id=event.project_id,
        payload_classification=event.payload_classification,
        metadata_json=metadata_json,
        integrity_digest=event.integrity_digest,
        result_digest=event.result_digest,
        payload_ciphertext=None,
        payload_nonce=None,
        payload_key_version=None,
        retention_at=event.retention_at,
    )
    if event.payload_mode is EventPayloadMode.DIGEST_ONLY:
        return record
    if keyring is None:
        raise ExecutionEventConflictError("event encryption key unavailable")
    payload_key_version, payload_nonce, payload_ciphertext = encrypt_bytes(
        keyring, _canonical_json(event.result_rows), _aad(record)
    )
    return replace(
        record,
        payload_nonce=payload_nonce,
        payload_key_version=payload_key_version,
        payload_ciphertext=payload_ciphertext,
    )


def _decode_event(
    record: StoredExecutionEvent,
    *,
    keyring: EventEncryptionKeyring | None,
    access: EventAccessContext,
    now: datetime,
) -> BusinessQueryExecutionEvent:
    if record.project_id != access.project_id or not _purpose_allowed(
        record.payload_classification, access.purpose
    ):
        raise ExecutionEventResolutionError("execution event not accessible")
    if record.tombstoned_at is not None:
        raise ExecutionEventResolutionError("execution event tombstoned")
    if now >= record.retention_at:
        raise ExecutionEventResolutionError("execution event expired")
    if (
        record.payload_ciphertext is None
        or record.payload_nonce is None
        or record.payload_key_version is None
    ):
        raise ExecutionEventResolutionError("execution event payload unavailable")
    if keyring is None:
        raise ExecutionEventResolutionError("execution event encryption key unavailable")
    try:
        raw_rows = decrypt_bytes(
            keyring,
            record.payload_key_version,
            record.payload_nonce,
            record.payload_ciphertext,
            _aad(record),
        )
        rows = json.loads(raw_rows)
        metadata = json.loads(record.metadata_json)
        members = tuple(ResultMember(**member) for member in metadata["result_members"])
        event = BusinessQueryExecutionEvent(
            **metadata,
            result_rows=_restore_typed_rows(members, rows),
            integrity_digest=record.integrity_digest,
        )
        event.validate_integrity()
        _validate_record_binding(record, event)
    except (InvalidTag, ValueError, json.JSONDecodeError, ExecutionEventConflictError) as exc:
        message = "execution event integrity check failed"
        if isinstance(exc, ValueError) and "key version" in str(exc):
            message = "execution event encryption key unavailable"
        raise ExecutionEventResolutionError(message) from exc
    return event
