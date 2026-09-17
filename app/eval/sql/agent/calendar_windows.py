"""Pure calendar window math for the SQL agent date tool.

Windows are inclusive ISO date strings (YYYY-MM-DD). Week bounds use ISO weeks
(Monday start). ``today`` is injected from the same value as the SQL system prefix.
"""

from __future__ import annotations

import calendar
import json
from datetime import date, timedelta
from typing import Any


def _iso(d: date) -> str:
    return d.isoformat()


def _month_end(year: int, month: int) -> date:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, last)


def calendar_windows(today: date) -> dict[str, Any]:
    """Return named inclusive windows relative to ``today``."""
    # ISO week: Monday=0 … Sunday=6
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    last_week_end = week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    month_start = date(today.year, today.month, 1)
    month_end = _month_end(today.year, today.month)
    if today.month == 1:
        last_month_start = date(today.year - 1, 12, 1)
        last_month_end = _month_end(today.year - 1, 12)
    else:
        last_month_start = date(today.year, today.month - 1, 1)
        last_month_end = _month_end(today.year, today.month - 1)

    year_start = date(today.year, 1, 1)
    year_end = date(today.year, 12, 31)
    last_year_start = date(today.year - 1, 1, 1)
    last_year_end = date(today.year - 1, 12, 31)

    return {
        "today": _iso(today),
        "this_week": {"start": _iso(week_start), "end": _iso(week_end)},
        "last_week": {"start": _iso(last_week_start), "end": _iso(last_week_end)},
        "this_month": {"start": _iso(month_start), "end": _iso(month_end)},
        "last_month": {"start": _iso(last_month_start), "end": _iso(last_month_end)},
        "this_year": {"start": _iso(year_start), "end": _iso(year_end)},
        "last_year": {"start": _iso(last_year_start), "end": _iso(last_year_end)},
    }


def format_calendar_windows(today: date) -> str:
    """JSON payload returned by the sql_calendar_windows tool."""
    return json.dumps(calendar_windows(today), separators=(",", ":"), sort_keys=True)
