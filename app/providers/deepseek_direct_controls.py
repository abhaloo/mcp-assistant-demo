"""Fail-closed DeepSeek first-party API wire controls for openai_compat requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.providers.model_registry import PolicyViolationError

ReasoningEffortWire = Literal["low", "high", "max"]

# Resolved-status vocabulary for the DeepSeek-direct SQL estimand -- the
# actual guard logic that reads these lives in app.experiments.estimand_guards;
# these two constants and the error class below stay here because they describe
# this provider's own wire-level estimand vocabulary, not eval-campaign
# orchestration.
DEEPSEEK_DIRECT_SQL_ESTIMAND_SUPPORTED = "SUPPORTED"
DEEPSEEK_DIRECT_SQL_ESTIMAND_UNSUPPORTED = "UNSUPPORTED_FOR_SQL"


@dataclass(frozen=True)
class DeepSeekDirectControls:
    """Wire-level DeepSeek Chat Completions constraints (thinking + reasoning_effort)."""

    thinking_enabled: bool
    reasoning_effort: ReasoningEffortWire


class DeepSeekDirectSqlEstimandError(PolicyViolationError):
    """Frozen contract declares the DeepSeek-direct SQL pair unsupported or unresolved."""


def normalize_deepseek_direct_effort(effort: str) -> ReasoningEffortWire:
    """Map profile effort to native DeepSeek wire values (``max`` stays ``max``)."""
    if effort == "xhigh":
        raise ValueError("xhigh is OpenRouter-only; use max for DeepSeek direct")
    if effort not in ("low", "high", "max"):
        raise ValueError(f"unsupported DeepSeek direct effort: {effort!r}")
    return effort  # type: ignore[return-value]


def build_deepseek_direct_extra(controls: DeepSeekDirectControls) -> dict:
    """Build ``extra_body`` for Chat Completions — no OpenRouter ``provider`` block."""
    body: dict = {"reasoning_effort": controls.reasoning_effort}
    if controls.thinking_enabled:
        body["thinking"] = {"type": "enabled"}
    return body
