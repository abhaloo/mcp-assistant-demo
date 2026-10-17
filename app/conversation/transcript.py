"""Transform and sanitize transcript turns before prompts or persistence (ADR 0020)."""

from __future__ import annotations

from typing import Literal

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.config import settings
from app.conversation.reference_artifact import ReferenceArtifact
from app.conversation.transcript_models import (
    TURN_CONTENT_MAX,
    BqTurnDigest,
    ContextMode,
    TranscriptTurn,
)
from app.guardrails.document_redaction import redact_documents_with_stats
from app.models.tool_results import ToolResultFields
from app.services.record_intent import RecordAggregateContinuation


def to_messages(turns: list[TranscriptTurn]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for t in turns:
        out.append(HumanMessage(t.content) if t.role == "user" else AIMessage(t.content))
    return out


def _redact_text(text: str) -> str:
    """Redact configured entities from a single transcript string (write-path default)."""
    if not settings.redaction_enabled or not text:
        return text
    docs = [Document(page_content=text)]
    return redact_documents_with_stats(docs).documents[0].page_content


def build_exchange_turns(
    question: str,
    answer: str,
    *,
    exchange_id: str | None = None,
    context_mode: ContextMode | None = None,
    reference_artifact: ReferenceArtifact | None = None,
    aggregate_continuation: RecordAggregateContinuation | None = None,
    bq_digest: BqTurnDigest | None = None,
    tool_results: ToolResultFields | None = None,
    answer_mode: Literal["explanation", "direct"] | None = None,
    source_exchange_ids: tuple[str, ...] = (),
    conversation_subject: str | None = None,
) -> list[TranscriptTurn]:
    """User+assistant turns for persistence: redact, truncate, skip empty answer.

    ``reference_artifact`` and ``bq_digest`` stamp onto the ASSISTANT turn only --
    never the user turn, mirroring ``context_mode``'s user-turn-only
    convention in reverse. When the answer is blank, no assistant turn is
    created at all, so those fields have nowhere to attach and are silently
    dropped rather than fabricating a turn to hold them.
    """
    from app.conversation.transcript_store import new_exchange_id

    eid = exchange_id or new_exchange_id()
    q = _redact_text(question)
    turns = [
        TranscriptTurn(
            role="user",
            content=q[:TURN_CONTENT_MAX],
            exchange_id=eid,
            context_mode=context_mode,
        )
    ]
    body = _redact_text(answer).strip()
    if body:
        turns.append(
            TranscriptTurn(
                role="assistant",
                content=body[:TURN_CONTENT_MAX],
                exchange_id=eid,
                reference_artifact=reference_artifact,
                aggregate_continuation=aggregate_continuation,
                bq_digest=bq_digest,
                answer_mode=answer_mode,
                source_exchange_ids=source_exchange_ids,
                conversation_subject=conversation_subject,
                **(tool_results or {}),
            )
        )
    return turns
