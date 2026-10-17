"""PII-scrubbing LangSmith capture client."""

from __future__ import annotations

import logging
import re
from typing import Any

from langsmith import Client

from app.config import settings
from app.guardrails.analyzer import detect_pii, get_anonymizer
from app.models.correlation_id import is_valid_correlation_id

logger = logging.getLogger(__name__)

_REDACTED = "<redacted>"
_PAGE_CONTEXT_REDACTED = "<PAGE_CONTEXT_REDACTED>"

_UNTRUSTED_BLOCK_RE = re.compile(
    r"BEGIN UNTRUSTED PAGE CONTEXT.*END UNTRUSTED PAGE CONTEXT",
    re.DOTALL,
)
_PAGE_CONTEXT_LINE_RE = re.compile(r"\(Page context: viewing [^)]*\)")
_RECORDS_FENCE_RE = re.compile(
    r"BEGIN PAGE RECORDS.*?END PAGE RECORDS",
    re.DOTALL | re.IGNORECASE,
)
# Trusted record-context fence (task A4, app/rag/chains/document_chain.py::
# format_record_context_block) — BEGIN/END RECORD CONTEXT <nonce>, mirroring
# _RECORDS_FENCE_RE above but ALSO consuming the trailing nonce on the END
# line ([^\n]*) so the nonce itself never survives redaction (A4.1 F1 fix:
# this pattern was entirely missing, so record field values egressed to
# LangSmith with only generic inline-PII masking).
_RECORD_CONTEXT_FENCE_RE = re.compile(
    r"BEGIN RECORD CONTEXT.*?END RECORD CONTEXT[^\n]*",
    re.DOTALL | re.IGNORECASE,
)

_DENY_KEYS = frozenset(
    {
        "page_context",
        "records",
        "policy",
        "fields",
        "truncated_fields",
        "profile",
        "title",
        "link_key",
        "truncated_fields",
    }
)

_CAPTURE_ENTITIES = [
    "PHONE_NUMBER",
    "PERSON",
    "EMAIL_ADDRESS",
    "CREDIT_CARD",
    "IBAN_CODE",
    "US_SSN",
    "LOCATION",
    "IP_ADDRESS",
    "URL",
]

_mask_warned = False
_capture_client: Client | None = None


def mask_text(text: str) -> str:
    global _mask_warned
    try:
        text = _UNTRUSTED_BLOCK_RE.sub(_PAGE_CONTEXT_REDACTED, text)
        text = _RECORDS_FENCE_RE.sub(_PAGE_CONTEXT_REDACTED, text)
        text = _RECORD_CONTEXT_FENCE_RE.sub(_PAGE_CONTEXT_REDACTED, text)
        text = _PAGE_CONTEXT_LINE_RE.sub(_PAGE_CONTEXT_REDACTED, text)
        detection = detect_pii(text, id_context_filter=False, entities=_CAPTURE_ENTITIES)
        if not detection.findings:
            return text
        result = get_anonymizer().anonymize(text=text, analyzer_results=detection.findings)
        return result.text
    except Exception:
        if not _mask_warned:
            logger.warning("LangSmith PII scrub failed; emitting redacted sentinel", exc_info=True)
            _mask_warned = True
        return _REDACTED


def scrub(data: Any, depth: int = 10) -> Any:
    if depth <= 0:
        return _REDACTED
    if isinstance(data, dict):
        return {
            key: _PAGE_CONTEXT_REDACTED
            if key in _DENY_KEYS or key == "page_context"
            else scrub(value, depth - 1)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [scrub(item, depth - 1) for item in data]
    if isinstance(data, str):
        return mask_text(data)
    return data


def build_capture_client() -> Client | None:
    global _capture_client

    if not settings.langsmith_tracing:
        _capture_client = None
        return None

    if not settings.langsmith_api_key:
        raise ValueError(
            "langsmith_api_key is required when langsmith_tracing is enabled. "
            "Set LANGSMITH_API_KEY in .env or disable LANGSMITH_TRACING."
        )

    _capture_client = Client(
        api_key=settings.langsmith_api_key,
        hide_inputs=scrub,
        hide_outputs=scrub,
    )
    return _capture_client


def get_capture_client() -> Client | None:
    return _capture_client


def record_feedback(
    run_id: str,
    *,
    verdict: str,
    comment: str | None = None,
    reason: str | None = None,
    source_info: dict | None = None,
) -> bool:
    client = get_capture_client()
    if client is None:
        return False
    # Correlation IDs are not LangSmith run IDs — skip rather than orphan feedback.
    if is_valid_correlation_id(run_id):
        return False
    try:
        client.create_feedback(
            run_id,
            key="user_verdict",
            score=1.0 if verdict == "up" else 0.0,
            comment=mask_text(comment) if comment else None,
            trace_id=run_id,
            source_info={
                **(source_info or {}),
                **({"reason": reason} if reason else {}),
            },
        )
    except Exception:
        logger.warning("feedback dropped after LangSmith error trace_id=%s", run_id, exc_info=True)
        return False
    return True
