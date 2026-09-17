"""Cryptographic digests and stable identifier generators for execution events."""

from __future__ import annotations

import hashlib
from typing import Any

from app.business_query.journaling.canonical_json import canonical_json_bytes as _canonical_json

_MAX_DETAIL_EVIDENCE_INPUT_BYTES = 4096


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def answer_query_id_for(idempotency_key: str) -> str:
    """Return a stable opaque executor ID without embedding run or case labels."""
    if not idempotency_key:
        raise ValueError("answer query idempotency key is required")
    return f"aq_{_sha256(idempotency_key.encode('utf-8'))[:32]}"


def _bounded_digest(value: Any) -> str:
    """Digest a bounded representation without persisting the source value."""
    payload = _canonical_json(value)
    if len(payload) > _MAX_DETAIL_EVIDENCE_INPUT_BYTES:
        payload = _canonical_json(
            {
                "oversize": True,
                "length": len(payload),
                "source_digest": _sha256(payload),
            }
        )
    return _sha256(payload)


def _aad(record: Any) -> bytes:
    return _canonical_json(
        {
            "answer_query_id": record.answer_query_id,
            "project_id": record.project_id,
            "integrity_digest": record.integrity_digest,
            "result_digest": record.result_digest,
        }
    )
