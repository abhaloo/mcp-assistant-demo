"""Domain contracts and value types for evidence retention and restore.

Follows Spec §7 and ADR 0076.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.outcomes import BusinessQueryWireOutcome
from app.models.citations import CitationsPayload, Source
from app.models.result_presentation import ResultPresentation
from app.models.tool_results import TurnResult
from app.rag.retrieval.document_contracts import DocumentProvenance

# Maximum serialized snapshot payload size: exactly 1 MiB (1,048,576 bytes)
MAX_SNAPSHOT_BYTES = 1_048_576

# Exactly 43 base64url characters (32 random bytes without padding)
_BASE64URL_REGEX = re.compile(r"^[A-Za-z0-9_-]{43}$")


class SnapshotTooLargeError(Exception):
    """Raised when serialized snapshot payload exceeds the 1 MiB limit."""


class RestoreReference(BaseModel):
    """Validated 43-character base64url restore reference."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    restore_ref: str = Field(min_length=43, max_length=43)

    @field_validator("restore_ref", mode="after")
    @classmethod
    def _validate_base64url(cls, v: str) -> str:
        if not _BASE64URL_REGEX.match(v):
            raise ValueError("restore_ref must be exactly 43 base64url characters")
        return v


class RestoreRequest(BaseModel):
    """Client request to restore one or more historical turns within a thread."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    thread_id: str = Field(min_length=1, max_length=64)
    references: tuple[RestoreReference, ...] = Field(min_length=1, max_length=40)

    @field_validator("references", mode="after")
    @classmethod
    def _validate_unique_references(
        cls, refs: tuple[RestoreReference, ...]
    ) -> tuple[RestoreReference, ...]:
        seen: set[str] = set()
        for r in refs:
            if r.restore_ref in seen:
                raise ValueError(f"duplicate restore_ref in request: {r.restore_ref}")
            seen.add(r.restore_ref)
        return refs


class RestoredTurn(BaseModel):
    """Server-retained turn contents authorized for display."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    thread_id: str
    run_id: str
    exchange_id: str
    answer_text: str
    sources: tuple[Source, ...] = ()
    citations: CitationsPayload
    business_query: BusinessQueryWireOutcome | None = None
    presentation: ResultPresentation | None = None
    turn_result: TurnResult

    def __repr__(self) -> str:
        # Safe repr: IDs only, no raw text or row data
        return (
            f"RestoredTurn(thread_id={self.thread_id!r}, "
            f"run_id={self.run_id!r}, exchange_id={self.exchange_id!r})"
        )


class RestoredEvidence(BaseModel):
    """Authorization verdict and optional restored turn payload for one reference."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    restore_ref: str
    status: Literal["authorized", "denied", "unavailable"]
    reason: Literal[
        "authorized",
        "policy_denied",
        "expired",
        "invalid_reference",
        "dependency_unverifiable",
        "unavailable",
    ]
    payload: RestoredTurn | None = None

    @classmethod
    def unavailable(cls, restore_ref: str) -> RestoredEvidence:
        """The withheld verdict: nothing found, nothing released."""
        return cls(
            restore_ref=restore_ref, status="unavailable", reason="unavailable", payload=None
        )

    @model_validator(mode="after")
    def _validate_payload_coherence(self) -> RestoredEvidence:
        if self.status == "authorized":
            if self.payload is None:
                raise ValueError("authorized restored evidence requires a payload")
            if self.reason != "authorized":
                raise ValueError("authorized restored evidence must have reason 'authorized'")
        else:
            if self.payload is not None:
                raise ValueError(f"payload forbidden when status is {self.status!r} (must be None)")
        return self


class RestoreResponse(BaseModel):
    """Transport response containing verdicts for requested restore references."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    version: Literal[1] = 1
    results: tuple[RestoredEvidence, ...]


class SnapshotBindings(BaseModel):
    """Metadata bindings authenticated by AEAD AAD for an evidence snapshot."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    project: str = "default"
    actor_id: str
    entity_id: int
    department_id: int | None = None
    thread_id: str
    run_id: str
    exchange_id: str
    schema_version: int = 1
    content_digest: str | None = None
    created_at: datetime
    expires_at: datetime
    tombstone: bool = False

    @field_validator("actor_id", mode="before")
    @classmethod
    def _normalize_actor_id(cls, v: object) -> str:
        return str(v)

    def __repr__(self) -> str:
        return (
            f"SnapshotBindings(project={self.project!r}, "
            f"actor_id={self.actor_id!r}, entity_id={self.entity_id}, "
            f"thread_id={self.thread_id!r}, digest={self.content_digest!r})"
        )


class NarrativeDependencies(BaseModel):
    """Typed dependencies required to reauthorize a semantic narrative answer."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    record_context_digest: str | None = None
    history_turn_ids: tuple[str, ...] = ()
    has_history: bool = False
    source_restore_refs: tuple[str, ...] = ()


class SnapshotPayload(BaseModel):
    """Encrypted plaintext payload stored inside an evidence snapshot."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    restored_turn: RestoredTurn
    stored_plans: tuple[StoredPlan, ...] = ()
    document_provenance: tuple[DocumentProvenance, ...] = ()
    narrative_dependencies: NarrativeDependencies | None = None

    @property
    def bq_plans(self) -> tuple[StoredPlan, ...]:
        return self.stored_plans

    @property
    def provenance(self) -> tuple[DocumentProvenance, ...]:
        return self.document_provenance

    def __repr__(self) -> str:
        return (
            f"SnapshotPayload(thread_id={self.restored_turn.thread_id!r}, "
            f"run_id={self.restored_turn.run_id!r})"
        )


@dataclass(frozen=True, repr=False)
class EvidenceSnapshot:
    """Domain representation of a fetched, decrypted, verified evidence snapshot."""

    restore_ref: str
    bindings: SnapshotBindings
    payload: SnapshotPayload

    @property
    def expires_at(self) -> datetime:
        return self.bindings.expires_at

    @property
    def tombstone(self) -> bool:
        return self.bindings.tombstone

    @property
    def actor_id(self) -> str:
        return self.bindings.actor_id

    @property
    def entity_id(self) -> int:
        return self.bindings.entity_id

    @property
    def department_id(self) -> int | None:
        return self.bindings.department_id

    @property
    def thread_id(self) -> str:
        return self.bindings.thread_id

    @property
    def run_id(self) -> str:
        return self.bindings.run_id

    @property
    def exchange_id(self) -> str:
        return self.bindings.exchange_id

    def __repr__(self) -> str:
        return (
            f"EvidenceSnapshot(restore_ref={self.restore_ref!r}, "
            f"thread_id={self.thread_id!r}, "
            f"expires_at={self.expires_at.isoformat()})"
        )
