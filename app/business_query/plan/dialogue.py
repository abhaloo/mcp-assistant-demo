"""Typed dialogue turns for the planner: an assistant turn carries the plan it ran."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.business_query.plan.plan_diff import PlanDigest
from app.business_query.plan.query_plan import BusinessPeriod


class UserTurn(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    role: Literal["user"] = "user"
    text: str = Field(min_length=1, max_length=2000)

    def __iter__(self) -> Iterator[Any]:
        return iter(render_dialogue_turn(self))


class AssistantTurn(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    role: Literal["assistant"] = "assistant"
    text: str = Field(min_length=1, max_length=200)
    answer_query_id: str | None = None
    digest: PlanDigest | None = None

    def __iter__(self) -> Iterator[Any]:
        return iter(render_dialogue_turn(self))


DialogueTurn = Annotated[UserTurn | AssistantTurn, Field(discriminator="role")]


def period_text(period: BusinessPeriod | None) -> str:
    """The period in dates, as the planner writes it; "none" when there is none."""
    if period is None:
        return "none"
    if period.between is not None:
        span = f"{period.between[0].isoformat()} to {period.between[1].isoformat()}"
    elif period.on is not None:
        span = f"on {period.on.isoformat()}"
    elif period.since is not None:
        span = f"since {period.since.isoformat()}"
    else:
        span = str(period.relative)
    by = f" by {period.granularity}" if period.granularity else ""
    return f"{period.time_dimension} {span}{by}"


def render_dialogue_turn(
    turn: UserTurn | AssistantTurn | tuple[Literal["ai", "human"], str],
) -> tuple[Literal["ai", "human"], str]:
    if isinstance(turn, tuple):
        return turn
    if isinstance(turn, UserTurn):
        return "human", turn.text
    if turn.digest is None:
        return "ai", turn.text
    d = turn.digest
    dims = ", ".join(d.dimensions) or "none"
    measures = ", ".join(d.measures) or "none"
    sets = ", ".join(d.set_ids) or "none"
    anchor = f", anchor {d.anchor}" if d.anchor else ""
    return "ai", (
        f"answered {turn.answer_query_id or '?'}: {d.grain}{anchor}; dimensions {dims}; "
        f"measures {measures}; limit {d.limit}; period {period_text(d.period)}; sets {sets}"
    )
