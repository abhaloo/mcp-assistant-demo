"""Authorized, bounded document retrieval adapter."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import asdict

from langchain_core.documents import Document

from app.auth import Principal
from app.core.bounded_executor import CapacityExhaustedError
from app.core.breakers import CircuitBreaker
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import TurnBudget, run_blocking_with_budget
from app.rag.access_tiers import ALL_TIERS, document_tiers_for
from app.rag.retrieval.document_contracts import (
    DocumentFailure,
    DocumentProvenance,
    DocumentSearchResult,
)
from app.rag.retrieval.document_fault import apply_document_fault
from app.rag.retrieval.document_policy import DocumentExecutionPolicy
from app.rag.retrieval.passages import RetrievedSource, retrieved_from_docs

logger = logging.getLogger(__name__)


def _cut_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _provenance_for(doc: Document, source: RetrievedSource) -> DocumentProvenance:
    meta = doc.metadata or {}
    chunk_index = meta.get("chunk_index", source.chunk_index)
    if chunk_index is not None:
        chunk_index = int(chunk_index)
    return DocumentProvenance(
        source_id=meta.get("source_id") or source.id or None,
        access_tier=meta.get("access_tier", source.access_tier),
        content_hash=meta.get("content_hash"),
        chunk_id=meta.get("chunk_id"),
        ingest_run_id=meta.get("ingest_run_id"),
        index_name=meta.get("index_name"),
        chunk_index=chunk_index,
    )


def _serialized_size(result: DocumentSearchResult) -> int:
    payload = {
        "passages": [asdict(item) for item in result.passages],
        "provenance": [asdict(item) for item in result.provenance],
        "truncated": result.truncated,
    }
    return len(json.dumps(payload, default=str).encode("utf-8"))


def normalize_documents(
    docs: list[Document],
    *,
    policy: DocumentExecutionPolicy,
    granted_tiers: list[str] | None = None,
) -> DocumentSearchResult | DocumentFailure:
    """Preserve order and markers; cut retained text on UTF-8 character bounds."""
    grants = set(granted_tiers or [])
    for doc in docs:
        tier = (doc.metadata or {}).get("access_tier")
        if tier is None:
            continue
        if grants and tier not in grants:
            if tier in ALL_TIERS:
                return DocumentFailure(status="denied", code="policy_denied")
            return DocumentFailure(status="denied", code="invalid_provenance")

    sources = retrieved_from_docs(docs)
    remaining = policy.max_passage_utf8_bytes
    passages: list[RetrievedSource] = []
    provenance: list[DocumentProvenance] = []
    truncated = False
    for doc, source in zip(docs, sources, strict=True):
        if remaining <= 0:
            truncated = True
            break
        cut, was_cut = _cut_utf8(source.content, remaining)
        if was_cut:
            truncated = True
        remaining -= len(cut.encode("utf-8"))
        passages.append(
            RetrievedSource(
                id=source.id,
                content=cut,
                source_file=source.source_file,
                title=source.title,
                access_tier=source.access_tier,
                chunk_index=source.chunk_index,
                marker=source.marker,
                section=source.section,
                resource_type=source.resource_type,
                record_id=source.record_id,
                label=source.label,
                link_key=source.link_key,
            )
        )
        provenance.append(_provenance_for(doc, source))

    result = DocumentSearchResult(
        passages=tuple(passages),
        provenance=tuple(provenance),
        truncated=truncated,
    )
    if _serialized_size(result) > policy.max_serialized_utf8_bytes:
        return DocumentFailure(status="failed", code="output_limit")
    return result


class DocumentExecutor:
    def __init__(
        self,
        retrieve: Callable[[str, list[str]], list[Document]],
        *,
        executor: Executor,
        policy: DocumentExecutionPolicy,
        breaker: CircuitBreaker,
        enabled: bool,
    ) -> None:
        self._retrieve = retrieve
        self._executor = executor
        self._policy = policy
        self._breaker = breaker
        self._enabled = enabled

    async def execute(
        self,
        *,
        query: str,
        principal: Principal,
        budget: TurnBudget,
    ) -> DocumentSearchResult | DocumentFailure:
        if not self._enabled:
            return DocumentFailure(status="unavailable", code="retriever_unavailable")
        try:
            apply_document_fault(self._policy.fault)
        except TimeoutError:
            return DocumentFailure(status="timeout", code="stage_timeout")
        if principal.document_tiers is not None:
            tiers = list(principal.document_tiers)
        else:
            tiers = document_tiers_for(principal)
        if not tiers:
            return DocumentSearchResult(passages=(), provenance=(), truncated=False)
        if not self._breaker.can_execute():
            return DocumentFailure(status="unavailable", code="circuit_open")
        budget.check_not_expired()

        def work() -> list[Document]:
            return self._retrieve(query, tiers)

        try:
            docs = await run_blocking_with_budget(
                work,
                budget,
                executor=self._executor,
                ceiling_seconds=self._policy.stage_ceiling_seconds,
                reserve_seconds=self._policy.commit_reserve_seconds,
                join_cancelled_child=False,
            )
        except asyncio.CancelledError:
            raise
        except DeadlineExpiredError:
            raise
        except CapacityExhaustedError:
            return DocumentFailure(status="unavailable", code="capacity_exhausted")
        except TimeoutError:
            return DocumentFailure(status="timeout", code="stage_timeout")
        except Exception as exc:  # noqa: BLE001
            # Absorb raw backend errors; the caller sees only a safe failure code.
            logger.info("document_retrieve_failed", extra={"error_type": type(exc).__name__})
            self._breaker.record_failure()
            return DocumentFailure(status="failed", code="retriever_unavailable")

        self._breaker.record_success()
        return normalize_documents(docs, policy=self._policy, granted_tiers=tiers)
