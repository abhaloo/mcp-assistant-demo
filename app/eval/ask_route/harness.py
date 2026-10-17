"""Wire plumbing shared by every ask-route eval run.

Mints a scoped token, parses an SSE body into events, and rebuilds an Answer
from those events. Transport mechanics only -- no model, no fixture store, no
route patching.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

import jwt

from app.config import settings
from app.eval.ask_route.case import ProdAskCase
from app.models.schemas import Answer, CitationsPayload, Source
from app.policy.manifest_loader import load_manifest_index
from app.rag.access_tiers import get_access_tiers


class AskHarnessError(RuntimeError):
    """A driver run could not produce a scoreable observation."""


@dataclass
class RetrievalCapture:
    """What the fixture store served, recorded for the retrieval-level checks."""

    granted_tiers: list[str] = field(default_factory=list)
    served_ids: list[str] = field(default_factory=list)


def record_access_claim(
    *,
    role: str,
    permissions: list[str],
    entity_id: int = 1,
    document_tiers: list[str] | None = None,
    resources: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    """The strict v2 ``record_access`` claim every eval caller mints.

    Document tiers derive from the permissions by the SAME production mapping
    the v1 path uses, never from a case's expected tiers: a claim built from
    the answer key would grant exactly what the tier check asserts, and
    widening permissions would no longer move anything for that check.
    """
    return {
        "schema_version": 2,
        "manifest_hash": load_manifest_index().current,
        "entity_id": entity_id,
        "cross_entity": False,
        "document_tiers": list(document_tiers or get_access_tiers(role, permissions)),
        "scope_values": {},
        "resources": {
            name: {"actions": list(actions), "field_sets": ["default"]}
            for name, actions in (resources or {}).items()
        },
    }


def mint_token(case: ProdAskCase, *, scope: str = "ask") -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": settings.rag_jwt_iss,
        "aud": settings.rag_jwt_aud,
        "iat": now,
        "exp": now + 60,
        "user_id": 1,
        "role": case.principal.role,
        "permissions": list(case.principal.permissions),
        "department_id": None,
        "jti": uuid.uuid4().hex,
        "scope": scope,
    }
    if case.principal.resources is not None:
        # Without this claim the caller is v1, and every reachability branch
        # in the route collapses to the same answer for every principal.
        claims["record_access"] = record_access_claim(
            role=case.principal.role,
            permissions=list(case.principal.permissions),
            document_tiers=list(case.principal.document_tiers or ()) or None,
            resources={name: list(a) for name, a in case.principal.resources.items()},
        )
    return jwt.encode(claims, settings.rag_jwt_secret, algorithm="HS256")


def parse_sse(body: str) -> list[tuple[str | None, object]]:
    """(event name, parsed data) for every SSE frame in the response body."""
    events: list[tuple[str | None, object]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name: str | None = None
        payload: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                payload.append(line[len("data:") :].strip())
        raw = "\n".join(payload)
        try:
            events.append((name, json.loads(raw) if raw else None))
        except json.JSONDecodeError:
            events.append((name, raw))
    return events


def answer_from_sse(case: ProdAskCase, events: list[tuple[str | None, object]]) -> Answer:
    """Rebuild the Answer contract from the SSE frames, for transport parity.

    The stream carries no answer field: the body is the concatenation of the
    token frames, which is the only text the browser ever renders.
    """
    text_parts: list[str] = []
    sources: list[Source] = []
    citations = CitationsPayload(parsed=False)
    done: dict = {}
    for name, data in events:
        if name == "token" and isinstance(data, dict):
            text_parts.append(str(data.get("d", "")))
        elif name == "sources" and isinstance(data, list):
            sources = [_source_from_stream(entry) for entry in data]
        elif name == "citations" and isinstance(data, dict):
            citations = CitationsPayload.model_validate(data)
        elif name == "done" and isinstance(data, dict):
            done = data
        elif name == "error":
            raise AskHarnessError(f"{case.id}: SSE emitted error {data}")
    if not done:
        raise AskHarnessError(f"{case.id}: SSE stream produced no done frame")
    return Answer(
        question=case.question,
        answer="".join(text_parts),
        sources=sources,
        model=str(done.get("model", "")),
        query_type=case.route,
        follow_up_suggestions=list(done.get("follow_up_suggestions", [])),
        citations=citations,
        thread_id=done.get("thread_id"),
        exchange_id=done.get("exchange_id"),
        trace_id=done.get("trace_id"),
        feedback_token=done.get("feedback_token"),
    )


def _source_from_stream(entry: object) -> Source:
    if not isinstance(entry, dict):
        raise AskHarnessError(f"unexpected sources entry: {entry!r}")
    source_id = entry.get("id")
    chunk_index: int | None = None
    if isinstance(source_id, str) and ":" in source_id:
        tail = source_id.rsplit(":", 1)[1]
        chunk_index = int(tail) if tail.isdigit() else None
    return Source(
        id=source_id if isinstance(source_id, str) else None,
        content=str(entry.get("snippet", "")),
        # The stream ships the basename as title; the checks compare basenames.
        source_file=str(entry.get("title", "unknown")),
        chunk_index=chunk_index,
        marker=entry.get("marker"),
    )
