"""Live driver: the real route, real models, with pass-through spies.

Nothing here replaces behaviour. The two seams that are wrapped call straight
through to the production callable and only record what crossed them — the
tiers the request resolved to, and the chunk ids retrieval returned before
citation filtering. Without those two facts the access-boundary check and the
"cited a source that was never retrieved" check cannot run at full strength,
and the suite would have to report them degraded on every live run.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.documents import Document

from app.eval.ask_route.case import AskObservation, ProdAskCase
from app.eval.ask_route.harness import (
    AskHarnessError,
    RetrievalCapture,
    answer_from_sse,
    mint_token,
    parse_sse,
)
from app.models.schemas import Answer


def _ids_of(docs: list[Document]) -> list[str]:
    ids: list[str] = []
    for index, doc in enumerate(docs):
        source = str(doc.metadata.get("source", "unknown"))
        chunk_index = doc.metadata.get("chunk_index")
        ids.append(f"{source}:{chunk_index}" if chunk_index is not None else f"{source}:{index}")
    return ids


@contextmanager
def _json_spy(capture: RetrievalCapture) -> Iterator[None]:
    from app.services.ask import _retrieve_documents as real_retrieve

    async def spy(  # noqa: PLR0913 - mirrors app.services.ask._retrieve_documents
        turn_input, access_tiers, config, *, principal, turn_budget=None, resources=None
    ):
        docs = await real_retrieve(
            turn_input,
            access_tiers,
            config,
            principal=principal,
            turn_budget=turn_budget,
            resources=resources,
        )
        capture.granted_tiers = list(access_tiers)
        capture.served_ids = _ids_of(docs)
        return docs

    with patch("app.services.ask._retrieve_documents", new=spy):
        yield


@contextmanager
def _sse_spy(capture: RetrievalCapture) -> Iterator[None]:
    from app.services.ask import _retrieve_documents as real_retrieve

    async def spy(  # noqa: PLR0913 - mirrors app.services.ask._retrieve_documents
        turn_input, access_tiers, config, *, principal, turn_budget=None, resources=None
    ):
        docs = await real_retrieve(
            turn_input,
            access_tiers,
            config,
            principal=principal,
            turn_budget=turn_budget,
            resources=resources,
        )
        capture.granted_tiers = list(access_tiers)
        capture.served_ids = _ids_of(docs)
        return docs

    with patch("app.services.ask._retrieve_documents", new=spy):
        yield


def _ask_body(case: ProdAskCase) -> dict:
    """Canonical v2 request (app/models/ask_v2_request.py); one run per call."""
    return {
        "operation": "new_question",
        "run_id": f"live-{case.id}-{uuid.uuid4().hex[:8]}",
        "thread_id": f"live-thread-{case.id}",
        "question": case.question,
        "idempotency_key": f"live-{case.id}-{uuid.uuid4().hex}",
        "deadline_at_ms": int(time.time() * 1000) + 10_000,
    }


def run_live_json(client: TestClient, case: ProdAskCase) -> AskObservation:
    capture = RetrievalCapture()
    with _json_spy(capture):
        response = client.post(
            "/api/ask",
            json=_ask_body(case),
            headers={"Authorization": f"Bearer {mint_token(case)}"},
        )
    if response.status_code != 200:
        raise AskHarnessError(f"{case.id}: live JSON ask returned {response.status_code}")
    return AskObservation(
        case_id=case.id,
        mode="live",
        transport="json",
        answer=Answer.model_validate(response.json()),
        retrieved_source_ids=capture.served_ids or None,
        granted_access_tiers=capture.granted_tiers or None,
    )


def run_live_sse(client: TestClient, case: ProdAskCase) -> AskObservation:
    capture = RetrievalCapture()
    with _sse_spy(capture):
        response = client.post(
            "/api/ask",
            json=_ask_body(case),
            headers={
                "Authorization": f"Bearer {mint_token(case)}",
                "Accept": "text/event-stream",
            },
        )
    if response.status_code != 200:
        raise AskHarnessError(f"{case.id}: live SSE ask returned {response.status_code}")
    return AskObservation(
        case_id=case.id,
        mode="live",
        transport="sse",
        answer=answer_from_sse(case, parse_sse(response.text)),
        retrieved_source_ids=capture.served_ids or None,
        granted_access_tiers=capture.granted_tiers or None,
    )
