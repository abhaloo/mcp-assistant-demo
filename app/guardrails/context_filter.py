"""
Context-aware post-filter for Presidio PHONE_NUMBER findings.

Suppresses candidates that look like bare ID tokens in ID-labelled prose
(invoice number, order ID, reference, etc.) while keeping dialable numbers.

Three-signal decision (see docs/insights-log.md 2026-05-28):
  1. ID cue in lookback — invoice, order, reference, …
  2. Contact/dial cue in lookback — overrides (1); call, dial, reach, …
  3. Dialable span shape — whitespace or hyphen grouping; overrides (1)

Only suppress when (1) is true AND (2) and (3) are false.

Aligned with Presidio guidance: post-analyzer filtering + held-out eval,
not threshold tuning alone. This filter is for free-text KB prose only;
structured DB rows need column-aware redaction (see guardrails README).

Limitation: run-together real phones near an ID trigger are kept only when
a dial verb appears in lookback; compact IDs with dial formatting are kept.
"""

import re

ID_CONTEXT_PATTERN = re.compile(
    r"\b("
    r"reference|ref|"
    r"order|"
    r"invoice|"
    r"account|acct|"
    r"txn|transaction|"
    r"tin|vrn|zrb|"
    r"control|voucher|receipt|cheque|"
    r"quotation|proforma|"
    r"work\s+order"
    r")\b",
    re.IGNORECASE,
)

# Dial/contact verbs observed in trap holdout + production corpus.
# Nouns (phone, mobile, switchboard) deliberately excluded — not justified as
# lookback counter-signals on the frozen holdout.
CONTACT_CONTEXT_PATTERN = re.compile(
    r"\b("
    r"call(?:ing|ed|s)?|"
    r"dial(?:ing|ed|s)?|"
    r"reach(?:es|ed|ing)?|"
    r"contact(?:ed|s|ing)?|"
    r"ring(?:ing|s)?"
    r")\b",
    re.IGNORECASE,
)

# Human dial formatting: internal space or hyphen groups (+255 712 …, +255-754-…).
DIALABLE_FORMAT_PATTERN = re.compile(r"[\s\-]")

LOOKBACK_CHARS = 40


def is_dialable_format(span_text: str) -> bool:
    """True when the span uses dial formatting rather than a bare digit run."""
    return bool(DIALABLE_FORMAT_PATTERN.search(span_text))


def is_id_context(text: str, span_start: int, span_end: int) -> bool:
    """
    True when the finding should be suppressed as an ID-shaped false positive.

    Requires an ID cue in lookback and neither a contact override nor dialable shape.
    """
    lookback = text[max(0, span_start - LOOKBACK_CHARS) : span_start]
    span = text[span_start:span_end]

    if not ID_CONTEXT_PATTERN.search(lookback):
        return False
    if CONTACT_CONTEXT_PATTERN.search(lookback):
        return False
    if is_dialable_format(span):
        return False
    return True


def filter_id_context(text: str, findings: list) -> list:
    """Drop findings whose surrounding text suggests they're IDs, not PII."""
    return [f for f in findings if not is_id_context(text, f.start, f.end)]
