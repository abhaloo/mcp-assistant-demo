"""Date window policies and prompt rules for the SQL agent."""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

FutureDatePolicy = Literal["exclude_future", "allow_future", "clarify"]
NamedDayIntent = Literal["on_day", "from_day", "clarify", "none"]

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)"
)
_MONTH_TO_NUM = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_EXPLICIT_FUTURE_CUE_RE = re.compile(
    r"\b(?:next\s+(?:month|quarter|year|week)|future|forecast|upcoming|projected)\b",
    re.IGNORECASE,
)
_MONTH_YEAR_RE = re.compile(rf"\b(?:{_MONTH})\s+(\d{{4}})\b", re.IGNORECASE)
_YEAR_MONTH_RE = re.compile(rf"\b(\d{{4}})\s+(?:{_MONTH})\b", re.IGNORECASE)
_MONTH_ONLY_RE = re.compile(rf"\b(?:in\s+)?({_MONTH})\b(?!\s+\d{{4}})", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_FUTURE_YEAR_RE = re.compile(r"\b(?:in\s+|for\s+)?(\d{4})\b")
_QUARTER_RE = re.compile(
    r"\b(?:q([1-4])|quarter\s+([1-4]))(?:\s+(?:of\s+)?(\d{4}))?\b",
    re.IGNORECASE,
)
_FULL_YEAR_AMBIGUOUS_RE = re.compile(
    r"\b(?:full|whole|entire)\s+year\b|\bthrough\s+(?:december|dec|year[\s-]?end)\b",
    re.IGNORECASE,
)

_DAY_SUFFIX = r"(?:st|nd|rd|th)?"
_NAMED_DAY = (
    rf"(?:\b(?:{_MONTH})\s+\d{{1,2}}{_DAY_SUFFIX}\b|"
    rf"\b\d{{1,2}}{_DAY_SUFFIX}\s+(?:of\s+)?(?:{_MONTH})\b)"
)

_NAMED_DAY_RE = re.compile(_NAMED_DAY, re.IGNORECASE)
_ON_DAY_CUE_RE = re.compile(
    rf"\bon\s+(?:the\s+)?(?:\d{{1,2}}{_DAY_SUFFIX}\s+(?:of\s+)?(?:{_MONTH})|"
    rf"(?:{_MONTH})\s+\d{{1,2}}{_DAY_SUFFIX})",
    re.IGNORECASE,
)
_CONFIRMED_FROM_DAY_RE = re.compile(
    r"\b(?:open[\s-]?ended|through\s+today|cumulative|"
    r"(?:from|since|starting)\s+(?:that\s+)?(?:day|date))\b",
    re.IGNORECASE,
)

FUTURE_DATE_RULES_EXCLUDE = """
FUTURE POST_DATE GUARD (default — applies to ledger revenue / journals.post_date filters):
- Exclude rows whose post_date is after Today's date unless the user explicitly asked for a
  future period. When aggregating by month or period within "this year", "this month", or
  "to date", clip at Today — do not include future-dated journal rows in totals or buckets."""

FUTURE_DATE_RULES_ALLOW = """
FUTURE POST_DATE GUARD (explicit future period requested):
- The user named a future calendar period relative to Today. Include post_date rows through
  that named future window — do not clip to Today when the question explicitly targets a
  future month, quarter, year, or date."""

FUTURE_DATE_RULES_CLARIFY = """
FUTURE POST_DATE GUARD (ambiguous full-year window):
- The user asked about a full-year or through-year-end window without clearly saying whether
  they mean year-to-date (post_date <= Today) or the complete calendar year including future
  months. Reply with CLARIFY: asking which they want — do not guess or emit SQL that silently
  includes or excludes future post_date rows."""

NAMED_DAY_RULES_ON = """
NAMED CALENDAR DAY (on-day — takes precedence over relative-date recency defaults):
- The user named a specific calendar day with an explicit "on" cue. Filter to that single day
  only (equality on the date column, e.g. DATE(post_date) = 'YYYY-MM-DD'). Do not use an
  open-ended >= filter or multi-day GROUP BY unless the user explicitly asked for a range or
  breakdown over time."""

NAMED_DAY_RULES_FROM = """
NAMED CALENDAR DAY (from-day — takes precedence over relative-date recency defaults):
- The user confirmed an open-ended window from a named calendar day. Use an open-ended lower
  bound (e.g. post_date >= 'YYYY-MM-DD') through Today unless they named an end. Do not collapse to
  single-day equality."""

NAMED_DAY_RULES_CLARIFY = """
NAMED CALENDAR DAY (ambiguous — takes precedence over relative-date recency defaults):
- The user named a calendar day (including with "from", "since", "starting", or "for") but has not
  confirmed single-day equality vs an open-ended range from that day. Reply with exactly one line
  starting with CLARIFY: asking whether they want that one day only ("on …") or cumulative/from
  that date through Today — do not guess, do not emit SQL with a silent window choice. This applies
  to any domain (revenue, jobs, invoices, etc.), not only revenue questions."""


def _month_token_to_num(token: str) -> int | None:
    return _MONTH_TO_NUM.get(token.lower())


def _named_month_year_is_future(question: str, today: date) -> bool:
    for match in _MONTH_YEAR_RE.finditer(question):
        month_token = match.group(0).split()[0]
        year = int(match.group(1))
        month_num = _month_token_to_num(month_token)
        if month_num is None:
            continue
        if date(year, month_num, 1) > today:
            return True
    for match in _YEAR_MONTH_RE.finditer(question):
        year = int(match.group(1))
        month_token = match.group(0).split()[-1]
        month_num = _month_token_to_num(month_token)
        if month_num is None:
            continue
        if date(year, month_num, 1) > today:
            return True
    return False


def _named_month_only_is_future(question: str, today: date) -> bool:
    for match in _MONTH_ONLY_RE.finditer(question):
        month_num = _month_token_to_num(match.group(1))
        if month_num is None:
            continue
        if month_num > today.month:
            return True
    return False


def _iso_date_is_future(question: str, today: date) -> bool:
    for match in _ISO_DATE_RE.finditer(question):
        candidate = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if candidate > today:
            return True
    return False


def _future_year_or_quarter(question: str, today: date) -> bool:
    for match in _QUARTER_RE.finditer(question):
        quarter_str = match.group(1) or match.group(2)
        if quarter_str is None:
            continue
        quarter = int(quarter_str)
        year_str = match.group(3)
        year = int(year_str) if year_str else today.year
        quarter_start_month = (quarter - 1) * 3 + 1
        if date(year, quarter_start_month, 1) > today:
            return True
    for match in _FUTURE_YEAR_RE.finditer(question):
        year = int(match.group(1))
        if year > today.year:
            return True
    return False


def future_date_policy(question: str, today: date) -> FutureDatePolicy:
    """Classify whether post_date filters should clip at Today or allow future rows."""
    q = question or ""
    if _EXPLICIT_FUTURE_CUE_RE.search(q):
        return "allow_future"
    if (
        _named_month_year_is_future(q, today)
        or _named_month_only_is_future(q, today)
        or _iso_date_is_future(q, today)
        or _future_year_or_quarter(q, today)
    ):
        return "allow_future"
    if _FULL_YEAR_AMBIGUOUS_RE.search(q) and today.month < 12:
        return "clarify"
    return "exclude_future"


def future_date_rules_block(policy: FutureDatePolicy) -> str:
    """Prompt micro-rule block for ``dated_prefix`` / revenue date filters."""
    if policy == "allow_future":
        return FUTURE_DATE_RULES_ALLOW
    if policy == "clarify":
        return FUTURE_DATE_RULES_CLARIFY
    return FUTURE_DATE_RULES_EXCLUDE


def classify_named_day_intent(question: str) -> NamedDayIntent:
    """Classify on-day vs confirmed from-day vs ambiguous named calendar day, or none.

    Only an explicit "on <named day>" cue is auto on_day. "from/since/starting" and other
    named-day mentions default to clarify (generalized window disambiguation).
    """
    q = question or ""
    if not _NAMED_DAY_RE.search(q):
        return "none"
    if _ON_DAY_CUE_RE.search(q):
        return "on_day"
    if _CONFIRMED_FROM_DAY_RE.search(q) and not _ON_DAY_CUE_RE.search(q):
        return "from_day"
    return "clarify"


def named_day_rules_block(intent: NamedDayIntent) -> str:
    """Prompt micro-rule block for ``dated_prefix`` when intent is not ``none``."""
    if intent == "on_day":
        return NAMED_DAY_RULES_ON
    if intent == "from_day":
        return NAMED_DAY_RULES_FROM
    if intent == "clarify":
        return NAMED_DAY_RULES_CLARIFY
    return ""
