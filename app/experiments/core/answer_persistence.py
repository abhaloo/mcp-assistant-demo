"""Sanitize answer rows before writing eval artifacts to disk."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.experiments.core.secret_redaction import redact_secrets
from app.providers.reasoning import (
    REASONING_KIND_PROVIDER_COT,
    REASONING_KWARG_KEYS,
    reasoning_call_entry_from_raw,
    reasoning_text_from_evidence,
)

# Full chain-of-thought bodies — gated by persist_eval_reasoning_text / _raw.
_COT_BODY_KEYS = frozenset({"reasoning_text", *REASONING_KWARG_KEYS})

# Non-body telemetry derived from CoT — safe to persist when present.
_COT_METADATA_KEYS = frozenset(
    {
        "reasoning_text_sha256",
        "reasoning_text_char_count",
        "reasoning_text_truncated",
        "reasoning_kind",
    }
)


def _sanitize_reasoning_evidence(evidence: dict[str, Any], *, persist_text: bool) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in evidence.items():
        if key in _COT_BODY_KEYS:
            if persist_text and key == "reasoning_text" and isinstance(value, str):
                sanitized[key] = redact_secrets(value)
            continue
        if key in _COT_METADATA_KEYS or key.startswith("native_tokens_"):
            sanitized[key] = value
    return sanitized


def _sanitize_response_metadata(metadata: dict[str, Any], *, persist_text: bool) -> dict[str, Any]:
    """Strip CoT bodies nested under ``response_metadata`` (baacad3 forward path)."""
    sanitized = dict(metadata)
    if persist_text:
        reasoning_text = sanitized.get("reasoning_text")
        if isinstance(reasoning_text, str):
            sanitized["reasoning_text"] = redact_secrets(reasoning_text)
    else:
        sanitized.pop("reasoning_text", None)
        for key in REASONING_KWARG_KEYS:
            sanitized.pop(key, None)
    evidence = sanitized.get("reasoning_evidence")
    if isinstance(evidence, dict):
        sanitized["reasoning_evidence"] = _sanitize_reasoning_evidence(
            evidence,
            persist_text=persist_text,
        )
    return sanitized


def _sanitize_reasoning_call(
    call: dict[str, Any], *, persist_text: bool, persist_raw: bool
) -> dict[str, Any]:
    """Campaign Reasoning Persistence list entry — dual bodies + metadata."""
    sanitized = {k: v for k, v in call.items() if k not in ("reasoning_text", "reasoning_text_raw")}
    raw = call.get("reasoning_text_raw")
    text = call.get("reasoning_text")
    if persist_raw and isinstance(raw, str):
        sanitized["reasoning_text_raw"] = raw
    if persist_text:
        source = text if isinstance(text, str) else raw
        if isinstance(source, str):
            sanitized["reasoning_text"] = redact_secrets(source)
    return sanitized


def _lift_reasoning_calls(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Build ``reasoning_calls`` from legacy evidence when SQL-style list is absent."""
    existing = row.get("reasoning_calls")
    if isinstance(existing, list) and existing:
        if any(not isinstance(c, dict) for c in existing):
            raise ValueError("reasoning_calls entries must be dicts (refuse non-dict leak path)")
        return [dict(c) for c in existing]

    evidence = row.get("reasoning_evidence")
    if not isinstance(evidence, dict):
        meta = row.get("response_metadata")
        evidence = (
            meta.get("reasoning_evidence")
            if isinstance(meta, dict) and isinstance(meta.get("reasoning_evidence"), dict)
            else {}
        )
    if not isinstance(evidence, dict):
        return []

    raw = reasoning_text_from_evidence(evidence)
    if not isinstance(raw, str) or not raw.strip():
        return []

    kind = None
    if isinstance(evidence.get("reasoning_kind"), str) and evidence["reasoning_kind"].strip():
        kind = evidence["reasoning_kind"]
    else:
        meta = row.get("response_metadata")
        if isinstance(meta, dict):
            candidate = meta.get("reasoning_kind")
            if isinstance(candidate, str) and candidate.strip():
                kind = candidate
    if kind is None:
        kind = REASONING_KIND_PROVIDER_COT
    return [reasoning_call_entry_from_raw(raw, index=0, reasoning_kind=kind)]


def sanitize_answer_row_for_persistence(row: dict[str, Any]) -> dict[str, Any]:
    """Apply Campaign Reasoning Persistence flags before writing eval jsonl."""
    sanitized = dict(row)
    persist_text = settings.persist_eval_reasoning_text
    persist_raw = settings.persist_eval_reasoning_raw

    if persist_text:
        reasoning_text = sanitized.get("reasoning_text")
        if isinstance(reasoning_text, str):
            sanitized["reasoning_text"] = redact_secrets(reasoning_text)
    else:
        sanitized.pop("reasoning_text", None)
        for key in REASONING_KWARG_KEYS:
            sanitized.pop(key, None)

    if not persist_raw:
        sanitized.pop("reasoning_text_raw", None)

    evidence = sanitized.get("reasoning_evidence")
    if isinstance(evidence, dict):
        sanitized["reasoning_evidence"] = _sanitize_reasoning_evidence(
            evidence,
            persist_text=persist_text,
        )
    response_metadata = sanitized.get("response_metadata")
    if isinstance(response_metadata, dict):
        sanitized["response_metadata"] = _sanitize_response_metadata(
            response_metadata,
            persist_text=persist_text,
        )

    reasoning_calls = [
        _sanitize_reasoning_call(c, persist_text=persist_text, persist_raw=persist_raw)
        for c in _lift_reasoning_calls(row)
    ]
    # Keep the persisted schema sparse for ordinary rows.  The list is an
    # additive evidence channel, not a required empty placeholder; omitting
    # it preserves byte-stable legacy artifacts while still persisting every
    # actual reasoning call when one is present.
    if reasoning_calls:
        sanitized["reasoning_calls"] = reasoning_calls
    else:
        sanitized.pop("reasoning_calls", None)
    return sanitized


def sanitize_answer_rows_for_persistence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [sanitize_answer_row_for_persistence(row) for row in rows]
