"""Text and number normalization shared by the production-ask quality checks.

Answers are markdown written for a browser panel, so a literal fact can appear
with soft hyphens, non-breaking spaces, thousands separators, or a currency
token in front of or behind the amount. Every check that compares an answer to
an oracle literal normalizes through this module, so one normalization rule is
in force everywhere instead of one per check.
"""

from __future__ import annotations

import re
import unicodedata

# A number as a reader would type it: optional thousands groups, optional
# decimals. The lookarounds stop a match inside an identifier or a date
# ("2026-01" yields 2026 and 01, never 202601).
_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")

# Zero-width and formatting characters a rich-text panel may carry through.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))

_MONEY_TOLERANCE = 0.01


def normalize_text(raw: str) -> str:
    """Casefolded, NFKC-normalized text with collapsed whitespace.

    NFKC folds full-width digits and non-breaking spaces onto their plain
    forms, so a fact still matches when the model emits a typographic variant.
    """
    folded = unicodedata.normalize("NFKC", raw).translate(_INVISIBLE)
    return " ".join(folded.split()).casefold()


def numeric_literals(raw: str) -> list[float]:
    """Every number in the text, thousands separators removed, in reading order."""
    normalized = unicodedata.normalize("NFKC", raw).translate(_INVISIBLE)
    values: list[float] = []
    for match in _NUMBER_RE.finditer(normalized):
        try:
            values.append(float(match.group(1).replace(",", "")))
        except ValueError:  # pragma: no cover - regex admits only float-parsable text
            continue
    return values


def numbers_match(expected: float, actual: float, *, tolerance: float = _MONEY_TOLERANCE) -> bool:
    """Absolute-tolerance comparison, wide enough for currency rounding only."""
    return abs(expected - actual) <= tolerance


def contains_number(raw: str, expected: float, *, tolerance: float = _MONEY_TOLERANCE) -> bool:
    return any(
        numbers_match(expected, value, tolerance=tolerance) for value in numeric_literals(raw)
    )


def contains_phrase(raw: str, phrase: str) -> bool:
    """Whitespace- and case-insensitive substring test on normalized text."""
    return normalize_text(phrase) in normalize_text(raw)


def currency_windows(raw: str, amount: float, *, window: int = 24) -> list[str]:
    """Text around each occurrence of ``amount``, for currency-token adjacency.

    Returns one window per matching numeric literal. An empty list means the
    amount is not present at all, which is an accuracy failure rather than a
    formatting one — the caller decides which of the two it is reporting.
    """
    normalized = unicodedata.normalize("NFKC", raw).translate(_INVISIBLE)
    windows: list[str] = []
    for match in _NUMBER_RE.finditer(normalized):
        try:
            value = float(match.group(1).replace(",", ""))
        except ValueError:  # pragma: no cover - see numeric_literals
            continue
        if not numbers_match(amount, value):
            continue
        start = max(0, match.start() - window)
        end = min(len(normalized), match.end() + window)
        windows.append(normalized[start:end])
    return windows
