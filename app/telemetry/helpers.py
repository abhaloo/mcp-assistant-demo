"""Shared OpenTelemetry helpers — errors, GenAI conventions, attribute setters."""

from __future__ import annotations

import hashlib
from typing import Protocol

from opentelemetry.trace import Span, Status, StatusCode

from app.config import settings


class HasTokenUsage(Protocol):
    """LangChain AIMessage shape for cross-provider token usage."""

    usage_metadata: dict[str, int] | None
    response_metadata: dict


def gen_ai_provider_name() -> str:
    """Map chat_provider config to OTel GenAI semconv provider name."""
    if settings.chat_provider == "azure":
        return "azure.ai.openai"
    return "openai"


def set_gen_ai_attributes(span: Span, *, operation: str) -> None:
    """Apply stable GenAI semantic convention attributes (no prompt content)."""
    span.set_attribute("gen_ai.operation.name", operation)
    span.set_attribute("gen_ai.request.model", settings.active_chat_model)
    span.set_attribute("gen_ai.provider.name", gen_ai_provider_name())


def cost_status_for_usage(input_tokens: int | None, output_tokens: int | None) -> str:
    """Tri-state cost status: 'complete' when both token counts were
    actually captured, 'partial' when usage is genuinely absent (or a stream was
    interrupted before it arrived) — never a fabricated zero standing in for either.
    'unknown' is reserved for unrecognized model pricing and is not
    produced by usage capture itself.
    """
    if input_tokens is not None and output_tokens is not None:
        return "complete"
    return "partial"


def token_usage_from_message(
    message: object,
) -> tuple[int | None, int | None, int | None]:
    """Extract (input_tokens, output_tokens, reasoning_tokens) from a message.

    Precedence:
    1. Modern ``usage_metadata`` (LangChain normalized format:
       ``input_tokens``, ``output_tokens``, ``output_token_details.reasoning``).
    2. Legacy ``response_metadata["token_usage"]`` or ``response_metadata["usage"]``
       (``prompt_tokens``, ``completion_tokens``, ``reasoning_tokens``).
    """
    if message is None:
        return None, None, None

    usage = getattr(message, "usage_metadata", None)
    if usage:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        reasoning = (usage.get("output_token_details") or {}).get("reasoning")
        return (
            int(input_tokens) if input_tokens is not None else None,
            int(output_tokens) if output_tokens is not None else None,
            int(reasoning) if reasoning is not None else None,
        )

    meta = getattr(message, "response_metadata", None) or {}
    token_usage = meta.get("token_usage") or meta.get("usage") or {}
    input_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
    output_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")
    reasoning = token_usage.get("reasoning_tokens")
    return (
        int(input_tokens) if input_tokens is not None else None,
        int(output_tokens) if output_tokens is not None else None,
        int(reasoning) if reasoning is not None else None,
    )


def set_gen_ai_usage(span: Span, message: HasTokenUsage) -> tuple[int, int] | None:
    """Stamp token counts off a LangChain ``AIMessage`` onto the span.

    Prefers ``usage_metadata`` (LangChain's normalized cross-provider shape:
    ``input_tokens`` / ``output_tokens``). Falls back to the raw provider
    counts on ``response_metadata`` (OpenAI shape: ``prompt_tokens`` /
    ``completion_tokens``) for providers that don't populate the normalized
    field. Returns ``(input, output)`` so the caller can also feed metrics, or
    ``None`` when the provider returned no usage at all.
    """
    input_tokens, output_tokens, _ = token_usage_from_message(message)
    if input_tokens is None or output_tokens is None:
        return None

    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    return input_tokens, output_tokens


def record_span_error(span: Span, exc: BaseException, *, set_error_status: bool = True) -> None:
    """Record exception event; set ERROR status for genuine system failures."""
    span.record_exception(exc)
    if set_error_status:
        span.set_status(Status(StatusCode.ERROR, str(exc)))


def record_span_failure_code(span: Span, code: str) -> None:
    """Mark a guarded call failed with a closed code; the exception text stays off the span."""
    span.set_attribute("guard.failure_code", code)
    span.set_status(Status(StatusCode.ERROR, code))


def record_access_tiers(span: Span, tiers: list[str]) -> None:
    """Attach resolved tier output for security debugging."""
    span.set_attribute("access.tiers_count", len(tiers))
    span.set_attribute("access.tiers", ",".join(sorted(tiers)))


def conversation_thread_hash(thread_id: str) -> str:
    """Salted SHA-256 hash of thread_id for privacy-safe telemetry."""
    return hashlib.sha256((settings.redaction_hmac_key + thread_id).encode()).hexdigest()[:12]


def record_conversation_attributes(
    span: Span,
    *,
    thread_id: str,
    turns: int,
    condensed: bool,
) -> None:
    """Stamp privacy-safe conversation attributes — never raw thread_id or content."""
    span.set_attribute("gen_ai.conversation.thread_hash", conversation_thread_hash(thread_id))
    span.set_attribute("gen_ai.conversation.turns", turns)
    span.set_attribute("gen_ai.conversation.condensed", condensed)


def record_retrieval_result(span: Span, docs: list) -> None:
    """Attach retrieval quality signals without document content."""
    count = len(docs) if docs is not None else 0
    span.set_attribute("retrieval.doc_count", count)
    span.set_attribute("retrieval.empty", count == 0)


def record_guardrails_redaction(
    span: Span,
    *,
    docs_in: int,
    docs_affected: int,
    entities_detected: int,
    entities_redacted: int,
    id_context_suppressed: int,
) -> None:
    span.set_attribute("guardrails.docs_in", docs_in)
    span.set_attribute("guardrails.docs_affected", docs_affected)
    span.set_attribute("guardrails.entities_detected", entities_detected)
    span.set_attribute("guardrails.entities_redacted", entities_redacted)
    span.set_attribute("guardrails.id_context_suppressed", id_context_suppressed)


def record_sql_anonymization(
    span: Span,
    *,
    tokens_created: int,
    redacted_values: int = 0,
    suppressed_values: int = 0,
    anonymize_calls: int,
    entities_detected: int,
) -> None:
    span.set_attribute("guardrails.tokens_created", tokens_created)
    span.set_attribute("guardrails.redacted_values", redacted_values)
    span.set_attribute("guardrails.suppressed_values", suppressed_values)
    span.set_attribute("guardrails.anonymize_calls", anonymize_calls)
    span.set_attribute("guardrails.entities_detected", entities_detected)


def record_citation_attributes(span: Span, *, parsed: bool, cited_count: int) -> None:
    span.set_attribute("citations.parsed", parsed)
    span.set_attribute("citations.cited_count", cited_count)


def record_provenance_attributes(
    span: Span, *, record_links_count: int, extraction_ok: bool
) -> None:
    span.set_attribute("record_links.count", record_links_count)
    span.set_attribute("provenance.extraction_ok", extraction_ok)
