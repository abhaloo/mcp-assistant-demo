"""RAG judge oracle implementations and factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.experiments.core.protocol import Oracle, OracleValidation, PreflightOracle


class PlainJudgeOracle:
    """Judge oracle without preflight."""

    is_paid = True

    def __init__(self, judge_fn: Callable[..., Any], judge_config: dict[str, Any]) -> None:
        self._judge_fn = judge_fn
        self._judge_config = judge_config

    def score(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], OracleValidation]:
        return self._judge_fn(rows, self._judge_config)


class PaidJudgeOracle(PlainJudgeOracle):
    """Paid judge oracle with canary preflight and budget handshake."""

    is_paid = True

    def preflight(self, config: dict[str, Any]) -> OracleValidation:
        del config
        return self._judge_fn.preflight(self._judge_config)

    def reset_for_attempt(self) -> None:
        reset = getattr(self._judge_fn, "reset_for_attempt", None)
        if callable(reset):
            reset()

    def mark_preflight_cost_accounted(self) -> None:
        mark = getattr(self._judge_fn, "mark_preflight_cost_accounted", None)
        if callable(mark):
            mark()

    def configure_budget(self, *, available_usd: float) -> None:
        configure = getattr(self._judge_fn, "configure_budget", None)
        if callable(configure):
            configure(available_usd=available_usd)


def rag_oracle_for(
    judge_fn: Callable[..., Any],
    judge_config: dict[str, Any],
) -> PreflightOracle | Oracle:
    """Return PaidJudgeOracle when judge_fn defines preflight, else PlainJudgeOracle."""
    if callable(getattr(judge_fn, "preflight", None)):
        return PaidJudgeOracle(judge_fn, judge_config)
    return PlainJudgeOracle(judge_fn, judge_config)
