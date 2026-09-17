"""Document search domain result and failure vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.rag.retrieval.passages import RetrievedSource


@dataclass(frozen=True)
class DocumentProvenance:
    source_id: str | None
    access_tier: str | None
    content_hash: str | None
    chunk_id: str | None
    ingest_run_id: str | None
    index_name: str | None
    chunk_index: int | None


@dataclass(frozen=True)
class DocumentSearchResult:
    passages: tuple[RetrievedSource, ...]
    provenance: tuple[DocumentProvenance, ...]
    truncated: bool


@dataclass(frozen=True)
class DocumentFailure:
    status: Literal["denied", "unavailable", "timeout", "failed"]
    code: Literal[
        "policy_denied",
        "circuit_open",
        "capacity_exhausted",
        "retriever_unavailable",
        "stage_timeout",
        "invalid_provenance",
        "output_limit",
    ]
