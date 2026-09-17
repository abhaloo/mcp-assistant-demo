"""Classify SQL agent execution errors into incomplete vs no_answer outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.ask_errors import INCOMPLETE_ANSWER_MESSAGE

SqlRunStopKind = Literal["incomplete", "no_answer"]


@dataclass(frozen=True)
class SqlRunStop:
    kind: SqlRunStopKind
    reason: str
    user_message: str


def classify_sql_agent_execution_error(exc: BaseException) -> SqlRunStop:
    text = str(exc)
    if text.startswith("budget_exceeded:"):
        reason = text.split(":", 1)[1] or "budget"
        return SqlRunStop("incomplete", reason, INCOMPLETE_ANSWER_MESSAGE)
    # Prod path wraps stall as budget_exceeded:stall_no_progress (above).
    # Bare stall_* is for direct/unwrapped errors in tests only.
    if text.startswith("stall_"):
        return SqlRunStop("incomplete", text, INCOMPLETE_ANSWER_MESSAGE)
    return SqlRunStop("no_answer", "no_answer", text)
