"""Zero-cost driver: the real ask route against a scripted model and fixture store.

The route runs for real: authentication, access-tier resolution, citation
finalization, source binding, follow-up selection, and both serializers. Only
two seams are replaced — the classifier and the generation call — and the
retriever is replaced by a fixture store that filters on exactly the tiers the
request resolved to.

What this proves and what it does not:

* It proves what the route DOES with a given model output, and which chunks a
  principal may reach.
* It does not prove that the model output was correct: the text is a fixture.
* It does not prove that the vector store applies its metadata filter. The
  fixture store applies the filter in Python; ChromaDB and Azure Search are
  not exercised here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from langchain_core.documents import Document

from app.config import settings
from app.eval.ask_route.case import AskObservation, ProdAskCase
from app.eval.ask_route.harness import (
    AskHarnessError,
    RetrievalCapture,
    answer_from_sse,
    mint_token,
    parse_sse,
)
from app.models.schemas import Answer


def _documents_for(case: ProdAskCase, access_tiers: list[str]) -> list[Document]:
    """Fixture chunks the granted tiers admit, in case order."""
    assert case.stub is not None
    granted = {tier.casefold() for tier in access_tiers}
    return [
        Document(
            page_content=doc.content,
            metadata={
                "source": doc.source_file,
                "chunk_index": doc.chunk_index,
                "section": doc.section,
                "access_tier": doc.access_tier,
            },
        )
        for doc in case.stub.docs
        if doc.access_tier.casefold() in granted
    ]


def _document_ids(docs: list[Document]) -> list[str]:
    return [f"{d.metadata['source']}:{d.metadata['chunk_index']}" for d in docs]


@contextmanager
def _conversation_disabled() -> Iterator[None]:
    """Run without the transcript store: no Redis dependency in a CI run."""
    original = settings.conversation_enabled
    settings.conversation_enabled = False
    try:
        yield
    finally:
        settings.conversation_enabled = original


def _chunks_for(text: str) -> list[MagicMock]:
    chunks: list[MagicMock] = []
    size = max(1, len(text) // 4 or 1)
    for start in range(0, len(text), size):
        chunk = MagicMock()
        chunk.content = text[start : start + size]
        chunk.usage_metadata = None
        chunk.response_metadata = {}
        chunks.append(chunk)
    return chunks


def run_stub_json(client: TestClient, case: ProdAskCase) -> AskObservation:
    """Drive POST /api/ask over JSON with the fixture store and scripted text."""
    if case.stub is None:
        raise AskHarnessError(f"{case.id}: no stub spec")
    capture = RetrievalCapture()

    async def fake_retrieve(turn_input, access_tiers, config) -> list[Document]:
        docs = _documents_for(case, list(access_tiers))
        capture.granted_tiers = list(access_tiers)
        capture.served_ids = _document_ids(docs)
        return docs

    generation = MagicMock()

    async def astream(*_args, **_kwargs):
        from app.telemetry.correlation import current_correlation_id
        from app.telemetry.invocation_ledger import ModelInvocationRecord
        from app.telemetry.invocation_payload import record_evidence_invocation

        cid = current_correlation_id()
        if cid:
            record_evidence_invocation(
                cid,
                ModelInvocationRecord(
                    scope="test",
                    correlation_id=cid,
                    purpose="rag_answer",
                    route_key="rk",
                    model="test-model",
                    request_messages="[]",
                    response_content=case.stub.model_answer,
                    reasoning_content=None,
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    latency_ms=1,
                    estimated_usd=None,
                    cost_status=None,
                ),
            )
        for chunk in _chunks_for(case.stub.model_answer):
            yield chunk

    generation.astream = astream

    with (
        _conversation_disabled(),
        patch("app.services.ask_prepare.classify_query", new=AsyncMock(return_value=case.route)),
        patch("app.services.ask._retrieve_documents", new=fake_retrieve),
        patch("app.services.semantic_answer.get_rag_generation_runnable", return_value=generation),
    ):
        response = client.post(
            "/api/ask",
            json={"question": case.question},
            headers={"Authorization": f"Bearer {mint_token(case)}"},
        )
    if response.status_code != 200:
        raise AskHarnessError(
            f"{case.id}: JSON ask returned {response.status_code}: {response.text}"
        )
    return AskObservation(
        case_id=case.id,
        mode="stub",
        transport="json",
        answer=Answer.model_validate(response.json()),
        retrieved_source_ids=capture.served_ids,
        granted_access_tiers=capture.granted_tiers,
    )


def run_stub_sse(client: TestClient, case: ProdAskCase) -> AskObservation:
    """Drive the same case over SSE and rebuild the answer from the events."""
    if case.stub is None:
        raise AskHarnessError(f"{case.id}: no stub spec")
    capture = RetrievalCapture()

    async def fake_retrieve(turn_input, access_tiers, config) -> list[Document]:
        docs = _documents_for(case, list(access_tiers))
        capture.granted_tiers = list(access_tiers)
        capture.served_ids = _document_ids(docs)
        return docs

    generation = MagicMock()

    async def astream(*_args, **_kwargs):
        from app.telemetry.correlation import current_correlation_id
        from app.telemetry.invocation_ledger import ModelInvocationRecord
        from app.telemetry.invocation_payload import record_evidence_invocation

        cid = current_correlation_id()
        if cid:
            record_evidence_invocation(
                cid,
                ModelInvocationRecord(
                    scope="test",
                    correlation_id=cid,
                    purpose="rag_answer",
                    route_key="rk",
                    model="test-model",
                    request_messages="[]",
                    response_content=case.stub.model_answer,
                    reasoning_content=None,
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    latency_ms=1,
                    estimated_usd=None,
                    cost_status=None,
                ),
            )
        for chunk in _chunks_for(case.stub.model_answer):
            yield chunk

    generation.astream = astream

    with (
        _conversation_disabled(),
        patch("app.services.ask_prepare.classify_query", new=AsyncMock(return_value=case.route)),
        patch("app.services.ask._retrieve_documents", new=fake_retrieve),
        patch("app.services.semantic_answer.get_rag_generation_runnable", return_value=generation),
    ):
        response = client.post(
            "/api/ask",
            json={"question": case.question},
            headers={
                "Authorization": f"Bearer {mint_token(case)}",
                "Accept": "text/event-stream",
            },
        )
    if response.status_code != 200:
        raise AskHarnessError(f"{case.id}: SSE ask returned {response.status_code}")
    events = parse_sse(response.text)
    return AskObservation(
        case_id=case.id,
        mode="stub",
        transport="sse",
        answer=answer_from_sse(case, events),
        retrieved_source_ids=capture.served_ids,
        granted_access_tiers=capture.granted_tiers,
    )
