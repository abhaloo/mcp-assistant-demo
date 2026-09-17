"""Pure calendar window math — hand-calculated oracles for eval_today=2026-06-16."""

from datetime import date

from app.eval.sql.agent.calendar_windows import calendar_windows


def test_calendar_windows_for_2026_06_16():
    """June 16, 2026 is a Tuesday; ISO week starts Monday 2026-06-15."""
    windows = calendar_windows(date(2026, 6, 16))
    assert windows["today"] == "2026-06-16"
    assert windows["this_week"] == {"start": "2026-06-15", "end": "2026-06-21"}
    assert windows["last_week"] == {"start": "2026-06-08", "end": "2026-06-14"}
    assert windows["this_month"] == {"start": "2026-06-01", "end": "2026-06-30"}
    assert windows["last_month"] == {"start": "2026-05-01", "end": "2026-05-31"}
    assert windows["this_year"] == {"start": "2026-01-01", "end": "2026-12-31"}
    assert windows["last_year"] == {"start": "2025-01-01", "end": "2025-12-31"}


def test_calendar_windows_january_last_month_rolls_year():
    windows = calendar_windows(date(2026, 1, 5))
    assert windows["last_month"] == {"start": "2025-12-01", "end": "2025-12-31"}
