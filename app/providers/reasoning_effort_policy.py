"""Per-model reasoning-effort validity. Fails closed before any network call."""

from __future__ import annotations

import re

# Vendor compatibility table (OpenAI reasoning-model matrix).
# Azure GPT-5 ladder (no xhigh). Attested 2026-08-03 for gpt-5.6-luna on Azure.
_AZURE_GPT5_EFFORTS = frozenset({"minimal", "low", "medium", "high"})
_AZURE_SUPPORTED_EFFORTS: dict[str, frozenset[str]] = {
    "gpt-5-nano": _AZURE_GPT5_EFFORTS,
    "gpt-5-mini": _AZURE_GPT5_EFFORTS,
    "gpt-5": _AZURE_GPT5_EFFORTS,
    # Azure gpt-5.6-luna deployment attested 2026-08-03 (prod-luna ARM snapshot).
    "gpt-5.6-luna": _AZURE_GPT5_EFFORTS,
}
# Official OpenAI API ladder for gpt-5.6-luna (developers.openai.com, 2026-08-12):
# none, low, medium (default), high, xhigh, max. Distinct from the Azure table.
_OPENAI_GPT56_LUNA_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_OPENAI_GPT5_EFFORTS = frozenset({"minimal", "low", "medium", "high"})
_OPENAI_SUPPORTED_EFFORTS: dict[str, frozenset[str]] = {
    "gpt-5.6-luna": _OPENAI_GPT56_LUNA_EFFORTS,
    "gpt-5-nano": _OPENAI_GPT5_EFFORTS,
}
# OpenRouter effort values; upstream support is verified by the effort probe.
OPENROUTER_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})
_OPENROUTER_EFFORTS = OPENROUTER_EFFORTS
_DEEPSEEK_DIRECT_EFFORTS = frozenset({"low", "high", "max"})

# A dated deployment suffix: gpt-5-nano-2025-08-07 -> gpt-5-nano.
_DATED_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}")


class UnsupportedReasoningEffortError(ValueError):
    def __init__(self, model: str, effort: str, supported: frozenset[str]) -> None:
        self.model, self.effort = model, effort
        super().__init__(
            f"Model {model!r} does not support reasoning_effort={effort!r}; "
            f"supported: {sorted(supported)}"
        )


def _table_key_for(normalized_model: str, table: dict[str, frozenset[str]]) -> str | None:
    """Exact table key, or a base name plus a dated-deployment suffix.

    A bare prefix is not enough: "gpt-5.6-luna" and "gpt-5-chat" both start with
    "gpt-5" but are different models whose ladders were never verified. Unknown
    models return None, which callers treat as "takes no effort parameter".
    """
    if normalized_model in table:
        return normalized_model
    for prefix in sorted(table, key=len, reverse=True):
        if not normalized_model.startswith(prefix):
            continue
        suffix = normalized_model[len(prefix) :]
        if _DATED_SUFFIX.fullmatch(suffix):
            return prefix
    return None


def supported_efforts(model: str, *, provider: str) -> frozenset[str] | None:
    """Valid ``reasoning_effort`` values for ``model`` on ``provider``.

    ``None`` means the model takes no effort parameter at all (a non-reasoning Azure
    deployment, or an unrecognised provider) — distinct from an empty set, which would
    mean "takes the parameter but nothing is valid".
    """
    if provider == "openrouter":
        return _OPENROUTER_EFFORTS
    if provider == "deepseek_direct":
        return _DEEPSEEK_DIRECT_EFFORTS
    if provider == "openai":
        normalized = model.strip().casefold()
        key = _table_key_for(normalized, _OPENAI_SUPPORTED_EFFORTS)
        if key is None:
            return None
        return _OPENAI_SUPPORTED_EFFORTS[key]
    if provider != "azure":
        return None
    normalized = model.strip().casefold()
    key = _table_key_for(normalized, _AZURE_SUPPORTED_EFFORTS)
    if key is None:
        return None
    return _AZURE_SUPPORTED_EFFORTS[key]


def default_effort(model: str) -> str | None:
    """The effort a profile that leaves ``reasoning_effort`` unset actually ran at.

    Reporting-only: never silently substitutes a value into a request.
    All listed Azure reasoning models default to ``medium`` per vendor table.
    """
    normalized = model.strip().casefold()
    key = _table_key_for(normalized, _AZURE_SUPPORTED_EFFORTS)
    if key is None:
        return None
    return "medium"


def assert_effort_supported(model: str, effort: str, *, provider: str) -> None:
    """Raise before any network call if ``model`` on ``provider`` rejects ``effort``."""
    supported = supported_efforts(model, provider=provider)
    if not supported or effort not in supported:
        raise UnsupportedReasoningEffortError(model, effort, supported or frozenset())
