"""Business-time and calendar arithmetic from bundle business date and timezone."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.business_query.outcomes import PlanRefused
from app.business_query.plan import BusinessPeriod, CompareShift, RelativeRange


def business_today(tz: str, *, now: datetime | None = None) -> date:
    """Calendar date in the business timezone (not UTC-today)."""
    try:
        timezone = ZoneInfo(tz)
    except ZoneInfoNotFoundError as error:
        # Bundle timezone misconfig is terminal — never AdapterUnsupported.
        raise PlanRefused("invalid_business_timezone") from error
    current = now if now is not None else datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    else:
        current = current.astimezone(timezone)
    return current.date()


def _relative_half_open(relative: RelativeRange, today: date) -> tuple[date, date]:
    """Half-open [start, end) for a closed relative range."""
    if relative == "today":
        return today, today + timedelta(days=1)

    week_start = today - timedelta(days=today.weekday())
    if relative == "this_week":
        return week_start, week_start + timedelta(days=7)
    if relative == "last_week":
        start = week_start - timedelta(days=7)
        return start, week_start

    month_start = date(today.year, today.month, 1)
    if today.month == 12:
        next_month = date(today.year + 1, 1, 1)
    else:
        next_month = date(today.year, today.month + 1, 1)
    if relative == "this_month":
        return month_start, next_month
    if relative == "last_month":
        if today.month == 1:
            start = date(today.year - 1, 12, 1)
        else:
            start = date(today.year, today.month - 1, 1)
        return start, month_start

    quarter = (today.month - 1) // 3
    q_start = date(today.year, quarter * 3 + 1, 1)
    if quarter == 3:
        q_end = date(today.year + 1, 1, 1)
    else:
        q_end = date(today.year, (quarter + 1) * 3 + 1, 1)
    if relative == "this_quarter":
        return q_start, q_end
    if relative == "last_quarter":
        if quarter == 0:
            start = date(today.year - 1, 10, 1)
            end = date(today.year, 1, 1)
        else:
            start = date(today.year, (quarter - 1) * 3 + 1, 1)
            end = q_start
        return start, end

    if relative == "this_year":
        return date(today.year, 1, 1), date(today.year + 1, 1, 1)
    if relative == "last_year":
        return date(today.year - 1, 1, 1), date(today.year, 1, 1)
    # Closed RelativeRange should make this unreachable; refuse terminally if not.
    raise PlanRefused("unsupported_relative_period")


def period_bounds(
    period: BusinessPeriod, tz: str, *, business_date: date | None = None
) -> tuple[date, date]:
    today = business_date or business_today(tz)
    if period.relative is not None:
        return _relative_half_open(period.relative, today)
    if period.on is not None:
        return period.on, period.on + timedelta(days=1)
    if period.since is not None:
        if period.since > today:
            raise PlanRefused("grain_unexpressible", check_site="since_in_future")
        return period.since, today + timedelta(days=1)
    assert period.between is not None
    # User-facing explicit dates are inclusive; SQL remains half-open.
    return period.between[0], period.between[1] + timedelta(days=1)


_CALENDAR_STEP: dict[str, tuple[str, int]] = {
    "today": ("days", 1),
    "this_week": ("days", 7),
    "last_week": ("days", 7),
    "this_month": ("months", 1),
    "last_month": ("months", 1),
    "this_quarter": ("months", 3),
    "last_quarter": ("months", 3),
    "this_year": ("months", 12),
    "last_year": ("months", 12),
}


def _shift_months(value: date, months: int) -> date:
    """Move a date by whole months, clamping the day to the target month's length."""
    index = value.year * 12 + (value.month - 1) + months
    year, month_index = divmod(index, 12)
    if year < date.min.year:
        raise PlanRefused("grain_unexpressible", check_site="comparison_out_of_range")
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _non_empty(start: date, end: date) -> tuple[date, date]:
    if start >= end:
        raise PlanRefused("grain_unexpressible", check_site="comparison_empty_range")
    return start, end


def shift_period(
    period: BusinessPeriod, bounds: tuple[date, date], shift: CompareShift
) -> tuple[date, date]:
    start, end = bounds
    if shift == "same_period_last_year":
        # Shift the first and last selected days, not the half-open end: shifting
        # 2028-03-01 alone lands on 2027-02-28 and empties a 28 Feb range.
        last_selected = end - timedelta(days=1)
        return _non_empty(
            _shift_months(start, -12), _shift_months(last_selected, -12) + timedelta(days=1)
        )
    step = _CALENDAR_STEP.get(period.relative) if period.relative is not None else None
    if step is None:
        span = end - start
        if span > start - date.min:
            raise PlanRefused("grain_unexpressible", check_site="comparison_out_of_range")
        return _non_empty(start - span, start)
    unit, count = step
    if unit == "days":
        delta = timedelta(days=count)
        return _non_empty(start - delta, end - delta)
    return _non_empty(_shift_months(start, -count), _shift_months(end, -count))


def comparison_bounds(
    period: BusinessPeriod,
    compare_to: BusinessPeriod | CompareShift,
    tz: str,
    *,
    business_date: date | None = None,
) -> tuple[tuple[date, date], tuple[date, date]]:
    current = period_bounds(period, tz, business_date=business_date)
    if isinstance(compare_to, BusinessPeriod):
        return current, period_bounds(compare_to, tz, business_date=business_date)
    return current, shift_period(period, current, compare_to)
