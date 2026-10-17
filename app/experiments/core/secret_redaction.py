"""Redact credentials from exception text before writing evidence artifacts.

Two passes: replace known Settings secret values, then regex key-shaped tokens.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

MASK = "<redacted>"

# Shortest secret worth masking. Below this, a value is far more likely to be a
# placeholder ("", "test", "key") whose replacement would shred unrelated text.
_MIN_SECRET_LEN = 8
# How much of a long secret's head must appear before we treat it as a leak.
# Covers pydantic's `head...tail` elision, where the full value never appears.
_PREFIX_LEN = 12
_PREFIX_MIN_SECRET_LEN = 16

# Fallback when Settings cannot be imported.
_FALLBACK_SECRET_FIELDS = (
    "model_api_key",
    "azure_client_secret",
    "unstructured_api_key",
    "deepseek_direct_api_key",
    "openrouter_api_key",
    "langsmith_api_key",
    "redaction_hmac_key",
    "rag_jwt_secret",
)


@lru_cache(maxsize=1)
def _secret_field_names() -> tuple[str, ...]:
    """Every Settings field classified as secret by config."""
    try:
        from app.config import Settings, _is_secret_setting

        derived = tuple(sorted(n for n in Settings.model_fields if _is_secret_setting(n)))
    except Exception:  # noqa: BLE001 - a redactor must never itself be the thing that raises
        return _FALLBACK_SECRET_FIELDS
    return derived or _FALLBACK_SECRET_FIELDS


_KEY_PATTERNS = (
    # OpenAI / OpenRouter style, with optional pydantic `...tail` elision.
    re.compile(r"sk-[A-Za-z0-9_\-]{4,}(?:\.\.\.[A-Za-z0-9_\-]+)?"),
    # JWT / Entra bearer token.
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
)


def redact_secrets(text: str | None, *, s: Any = None) -> str | None:
    """Return ``text`` with credential material masked. ``None``/empty pass through."""
    if not text:
        return text

    if s is None:
        from app.config import settings as s  # local: keeps import-time cost off callers

    out = text
    for field in _secret_field_names():
        value = getattr(s, field, "") or ""
        if not isinstance(value, str) or len(value) < _MIN_SECRET_LEN:
            continue
        if value in out:
            out = out.replace(value, f"<redacted:{field}>")
        if len(value) >= _PREFIX_MIN_SECRET_LEN:
            # Swallow whatever follows the prefix — including pydantic's
            # `head...tail` elision — so masking never leaves the tail behind.
            elided = re.compile(
                re.escape(value[:_PREFIX_LEN]) + r"[A-Za-z0-9_\-]*(?:\.\.\.[A-Za-z0-9_\-]+)?"
            )
            out = elided.sub(f"<redacted:{field}>", out)

    for pattern in _KEY_PATTERNS:
        out = pattern.sub(MASK, out)
    return out
