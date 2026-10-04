"""Time-group selections: a derived set whose key is a calendar bucket of one time member.

A time group selects whole calendar periods, for example the month with the highest
revenue. The answer plan then keeps the rows dated inside the selected periods.
This module holds typed values only and imports no plan model.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TimeGranularity = Literal["day", "week", "month", "quarter", "year"]
MAX_SELECTED_PERIODS = 50


class TimeGroupKey(BaseModel):
    """The time member and the calendar bucket a time-group set selects."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    time_dimension: str
    granularity: TimeGranularity


class SelectedPeriod(BaseModel):
    """One selected bucket: its first business day and its bounds on the stored column."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    start: date
    lower: datetime
    upper: datetime


class ResolvedTimeGroup(BaseModel):
    """The periods one read selected, in rank order.

    ``tie_beyond_limit`` is True when the next ranked period has the same measure
    value as the last selected one, so the answer must say a tie was broken.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    set_id: str
    key: TimeGroupKey
    periods: tuple[SelectedPeriod, ...] = Field(max_length=MAX_SELECTED_PERIODS)
    tie_beyond_limit: bool = False


def time_group_violations(*, mode: str, grain: str, measure_count: int) -> list[str]:
    """The rules a time-group set adds to the derived-set rules, as repair text."""
    out: list[str] = []
    if mode == "complete":
        out.append(
            "a time-group set picks its top periods: use mode pick with one measure and a limit"
        )
    if grain != "grouped" or measure_count != 1:
        out.append("a time-group set groups by its key with exactly one measure")
    return out
