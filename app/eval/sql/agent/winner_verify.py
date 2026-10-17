"""Singular winner verification for busiest / top entity questions."""

from __future__ import annotations

import re

from app.rag.model_router import EXPLICIT_MULTIROW_RE as _EXPLICIT_MULTIROW_RE

# Frozen grammar for E3 winner-intent detection. Do not OR-expand without contract revision.
_WINNER_VERIFY_RE = re.compile(
    r"\b(?:"
    r"busiest|"
    r"top\s+customer|"
    r"(?:most|highest|largest|biggest)\s+(?:jobs?|orders?|customers?)|"
    r"which\s+customer\s+has\s+the\s+most|"
    r"who\s+(?:is\s+)?(?:our\s+)?(?:busiest|top)\s+customer|"
    r"customer\s+with\s+(?:the\s+)?most"
    r")\b",
    re.IGNORECASE,
)

WINNER_VERIFY_RULES = """

SINGULAR WINNER VERIFICATION (busiest / top entity — one winner only):
- The user asks for a single top or busiest entity (customer with most jobs, busiest customer,
  etc.). Before answering, verify the winner with a grouped aggregate — do not trust an ad-hoc
  1-row query without checking for ties.
- Run COUNT or SUM grouped by the entity, ORDER BY the metric DESC, LIMIT 2.
- If the top two rows share the same metric value (near-tie), reply with CLARIFY: asking
  whether they want one winner or a list of tied entities — do not guess.
- Otherwise answer with the top row only (exactly one entity)."""


def needs_winner_verify(question: str) -> bool:
    """True when the question asks for a singular busiest / top entity winner."""
    q = question or ""
    if _EXPLICIT_MULTIROW_RE.search(q):
        return False
    return bool(_WINNER_VERIFY_RE.search(q))


def winner_verify_rules_block(active: bool) -> str:
    """Prompt micro-rule block for ``dated_prefix`` when winner verification applies."""
    return WINNER_VERIFY_RULES if active else ""
