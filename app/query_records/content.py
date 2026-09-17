"""Question redaction and fingerprint helpers for Query Record content columns."""

from __future__ import annotations

import hashlib
import hmac
import re

from app.config import settings
from app.guardrails.analyzer import detect_pii, get_anonymizer
from app.telemetry.langsmith_capture import mask_text

_API_TOKEN_RE = re.compile(r"sk-live-[A-Za-z0-9]+")
_CONNECTION_STRING_RE = re.compile(r"\w+://[^\s]+")
_INVOICE_ID_RE = re.compile(r"INV-\d{4}-\d+")


def question_fingerprint(question: str) -> str:
    """Stable SHA-256 hex digest of the verbatim question."""
    normalized = question.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def redact_question(question: str) -> str:
    """Reuse the LangSmith PII scrub seam plus durable-storage secret patterns."""
    text = mask_text(question)
    text = _API_TOKEN_RE.sub("<redacted>", text)
    text = _CONNECTION_STRING_RE.sub("<redacted>", text)
    text = _INVOICE_ID_RE.sub("<redacted>", text)
    org_detection = detect_pii(text, id_context_filter=False, entities=["ORGANIZATION"])
    if org_detection.findings:
        text = get_anonymizer().anonymize(text=text, analyzer_results=org_detection.findings).text
    return text


def subject_digest(subject_id: str) -> str:
    """Full 64-char HMAC-SHA256 hex for erasure keys (not the 12-char span idiom)."""
    return hmac.new(
        settings.redaction_hmac_key.encode("utf-8"),
        subject_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def prepare_question_columns(*, question: str, raw_capture_enabled: bool) -> dict[str, str | None]:
    """Build redacted/fingerprint/raw columns per D3."""
    return {
        "redacted_question": redact_question(question),
        "question_fingerprint": question_fingerprint(question),
        "raw_question": question if raw_capture_enabled else None,
    }
