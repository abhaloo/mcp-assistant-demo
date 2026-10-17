"""What one Ask turn showed the person, kept on its Query Record (ADR 0085).

The stream hands the answer text, the turn result, the follow-up chips and the
step timeline to the turn's own row with its terminal write. The row keeps them
as the person saw them, with no expiry, for analysis and to rebuild a thread's
memory. The owner reads them through a read-only role (ADR 0085 § Who reads).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.conversation.evidence.contracts import (
    NO_TIMELINE,
    RestoredStep,
    RestoredThought,
    TurnTimeline,
)
from app.models.ask_v2_events import FollowUpAction
from app.models.tool_results import TurnResult


class TurnDetail(BaseModel):
    """Version 1 of the JSON kept in ``turn_detail_json``."""

    model_config = ConfigDict(frozen=True)

    version: Literal[1] = 1
    turn_result: TurnResult | None = None
    follow_ups: tuple[FollowUpAction, ...] = ()
    unanswered_part: str | None = None
    steps: tuple[RestoredStep, ...] = ()
    thoughts: tuple[RestoredThought, ...] = ()
    duration_ms: int | None = None


@dataclass(frozen=True)
class TurnContent:
    """What one turn showed, handed to its Query Record with the terminal write."""

    operation: str
    exchange_id: str | None = None
    answer_text: str | None = None
    turn_result: TurnResult | None = None
    follow_ups: tuple[FollowUpAction, ...] = ()
    unanswered_part: str | None = None
    timeline: TurnTimeline = NO_TIMELINE


def turn_content_columns(content: TurnContent) -> dict[str, str | None]:
    """The row's four turn content columns, as shown (ADR 0085 § Columns)."""
    timeline = content.timeline
    detail = TurnDetail(
        turn_result=content.turn_result,
        follow_ups=content.follow_ups,
        unanswered_part=content.unanswered_part,
        steps=timeline.steps,
        thoughts=timeline.thoughts,
        duration_ms=timeline.duration_ms,
    )
    return {
        "exchange_id": content.exchange_id,
        "operation": content.operation,
        "answer_text": content.answer_text or None,
        "turn_detail_json": detail.model_dump_json(),
    }
