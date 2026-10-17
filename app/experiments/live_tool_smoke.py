"""Live B0 OpenRouter tool smoke — acquire → invoke → commit under spend ledger."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import settings
from app.experiments.arming import require_armed
from app.experiments.live_errors import LiveToolSmokeError
from app.experiments.spend_coordinator import (
    acquire,
    charge_for,
    commit,
    release,
)
from app.experiments.tool_smoke import ToolSmokeResult, run_tool_smoke
from app.providers.factory import get_chat_model
from app.providers.model_purpose import ModelPurpose
from app.providers.openrouter_controls import default_eval_controls

EXPERIMENT_MODEL = "deepseek/deepseek-v4-flash-0731"
PHASE = "tool_smoke"
# Conservative reserve for ~6 chat turns (5 tool + 1 final) on flash pricing.
DEFAULT_EST_USD = 0.25
# Double-arming so pytest/script accidents with a loaded .env cannot spend.
B0_LIVE_ENV = "DEEPSEEK_B0_LIVE"


def _require_live_armed() -> None:
    require_armed(
        B0_LIVE_ENV,
        purpose="live tool smoke (prevents accidental OpenRouter calls)",
    )
    if not settings.openrouter_api_key:
        raise LiveToolSmokeError("live tool smoke refused: OPENROUTER_API_KEY unset")


def run_live_tool_smoke(
    *,
    model_id: str = EXPERIMENT_MODEL,
    est_usd: float = DEFAULT_EST_USD,
    required_turns: int = 5,
    ledger_path: Path | None = None,
    acquire_fn: Callable[..., str] = acquire,
    commit_fn: Callable[..., None] = commit,
    release_fn: Callable[..., None] = release,
    get_model_fn: Callable[..., Any] = get_chat_model,
    run_smoke_fn: Callable[..., ToolSmokeResult] = run_tool_smoke,
) -> ToolSmokeResult:
    """Acquire spend, run the native-tool oracle against OpenRouter, commit actuals."""
    _require_live_armed()

    reservation_id = acquire_fn(
        PHASE,
        model_id,
        est_usd,
        ledger_path=ledger_path,
    )
    try:
        llm = get_model_fn(
            purpose=ModelPurpose.eval,
            deployment=model_id,
            controls=default_eval_controls(),
        )
    except Exception:
        release_fn(reservation_id, ledger_path=ledger_path)
        raise

    try:
        result = run_smoke_fn(llm, required_turns=required_turns)
    except Exception:
        # Provider invocation may have spent before failing without returning usage.
        # Charge the full reservation rather than treating an unknown live call as free.
        commit_fn(reservation_id, est_usd, ledger_path=ledger_path)
        raise

    evidence = result.evidence or {}
    actual = charge_for(
        model_id,
        input_tokens=int(evidence.get("input_tokens") or 0),
        output_tokens=int(evidence.get("output_tokens") or 0),
        reserved_usd=est_usd,
    )
    commit_fn(reservation_id, actual, ledger_path=ledger_path)
    return result
