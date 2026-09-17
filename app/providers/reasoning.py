"""Normalize reasoning-model answers: prefer content, drop reasoning fields."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from langchain_core.messages import BaseMessage

_REDACTED_THINKING_RE = re.compile(
    r"<think>.*?</think>\s*",
    re.DOTALL,
)

REASONING_KWARG_KEYS = frozenset({"reasoning_content", "reasoning", "reasoning_details"})
_REASONING_KWARG_KEYS = REASONING_KWARG_KEYS

# Campaign persistence provenance (non-body metadata — survives body-flag strip).
REASONING_KIND_PROVIDER_COT = "provider_cot"
REASONING_KIND_AZURE_SUMMARY = "azure_summary"


def normalize_answer_text(text: str) -> str:
    """Strip legacy ``<think>`` blocks from answer text."""
    return _REDACTED_THINKING_RE.sub("", text)


def should_normalize_final(msg: BaseMessage) -> bool:
    """Return True for final assistant answers that should drop reasoning fields."""
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        return False
    tool_call_chunks = getattr(msg, "tool_call_chunks", None) or []
    return not tool_call_chunks


def preserve_reasoning_for_replay(msg: BaseMessage) -> bool:
    """Return True when reasoning fields must survive for tool-call replay."""
    return not should_normalize_final(msg)


def reasoning_text_from_evidence(evidence: Mapping[str, Any]) -> str | None:
    """Resolve a non-empty reasoning body from an evidence/kwargs mapping."""
    raw = evidence.get("reasoning_text")
    if isinstance(raw, str) and raw.strip():
        return raw
    for key in ("reasoning_content", "reasoning"):
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return value
    details = evidence.get("reasoning_details")
    if isinstance(details, list):
        parts = [
            str(part.get("text") or part.get("content") or "")
            for part in details
            if isinstance(part, dict)
        ]
        joined = "\n".join(part for part in parts if part.strip())
        return joined or None
    return None


def extract_reasoning_evidence(
    msg: BaseMessage, *, max_chars: int | None = 4000
) -> dict[str, object]:
    """Pull provider reasoning fields for eval telemetry (before strip).

    ``max_chars`` truncates ``reasoning_text`` for legacy evidence blobs.
    Pass ``max_chars=None`` for Campaign Reasoning Persistence (no truncate).
    """
    kwargs = dict(msg.additional_kwargs or {})
    evidence: dict[str, object] = {}
    for key in _REASONING_KWARG_KEYS:
        value = kwargs.get(key)
        if value is None:
            continue
        evidence[key] = value
    text = reasoning_text_from_evidence(evidence)
    if text is not None:
        if max_chars is None or len(text) <= max_chars:
            evidence["reasoning_text"] = text
            evidence["reasoning_text_truncated"] = False
        else:
            evidence["reasoning_text"] = text[:max_chars]
            evidence["reasoning_text_truncated"] = True
        evidence["reasoning_text_char_count"] = len(text)
        evidence["reasoning_text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return evidence


def reasoning_call_entry_from_raw(
    raw: str,
    *,
    index: int,
    reasoning_kind: str | None = None,
) -> dict[str, object]:
    """Canonical ``reasoning_calls`` entry shape (untruncated; sanitize redacts later)."""
    entry: dict[str, object] = {
        "i": index,
        "reasoning_text_raw": raw,
        "reasoning_text": raw,
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "char_count": len(raw),
    }
    if isinstance(reasoning_kind, str) and reasoning_kind.strip():
        entry["reasoning_kind"] = reasoning_kind
    return entry


def _reasoning_kind_from_message(msg: BaseMessage) -> str | None:
    meta = getattr(msg, "response_metadata", None) or {}
    if isinstance(meta, dict):
        kind = meta.get("reasoning_kind")
        if isinstance(kind, str) and kind.strip():
            return kind
    return None


def build_reasoning_call_entry(msg: BaseMessage, *, index: int) -> dict[str, object] | None:
    """One ``reasoning_calls`` list entry for campaign persistence (untruncated).

    Returns None when there is no non-empty reasoning body (skip empties).
    """
    evidence = extract_reasoning_evidence(msg, max_chars=None)
    raw = evidence.get("reasoning_text")
    if not isinstance(raw, str) or not raw.strip():
        return None
    kind = _reasoning_kind_from_message(msg)
    if kind is None:
        kind = REASONING_KIND_PROVIDER_COT
    return reasoning_call_entry_from_raw(raw, index=index, reasoning_kind=kind)


def normalize_answer_content(msg: BaseMessage) -> BaseMessage:
    """Prefer message ``content``; drop ``reasoning_content`` from additional_kwargs."""
    content = msg.content
    if isinstance(content, str):
        content = normalize_answer_text(content)

    additional_kwargs = {
        k: v for k, v in (msg.additional_kwargs or {}).items() if k not in _REASONING_KWARG_KEYS
    }

    return msg.model_copy(update={"content": content, "additional_kwargs": additional_kwargs})
