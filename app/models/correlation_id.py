"""Correlation ID validation (matches Billing CorrelationId rule)."""

from __future__ import annotations

import re

_CORRELATION_ID_RE = re.compile(r"\A[a-f0-9]{32}\Z")
_ALL_ZEROS = "0" * 32


def is_valid_correlation_id(value: str) -> bool:
    """Return True when ``value`` is exactly 32 lowercase hex and not all zeros."""
    if not _CORRELATION_ID_RE.fullmatch(value):
        return False
    return value != _ALL_ZEROS


def validate_correlation_id(value: str | None) -> str | None:
    """Validate optional correlation ID; raise ValueError when strict rules fail."""
    if value is None:
        return None
    if not _CORRELATION_ID_RE.fullmatch(value):
        raise ValueError("run_id must be exactly 32 lowercase hexadecimal characters")
    if value == _ALL_ZEROS:
        raise ValueError("run_id must not be an all-zero value")
    return value
