"""Pre-generation escalation gate for the SQL agent.

Routes the hard receivables-aggregation pattern (per-invoice value minus payments,
then an outer aggregate) to the stronger model. gpt-4o-mini fails these *silently*
(valid but logically wrong SQL, no error), and no cheap post-generation signal
reliably catches confident-consistent-wrong SQL -- so hardness is predicted from
the question text instead.

The pattern is derived from standard accounts-receivable vocabulary (including
payment-status terms like overpaid, fully paid, partially paid), not from eval
questions, so it generalizes (for example it catches "more than 60 days overdue"
via "overdue"). This is a zero-shot rule: no labeled data, no trained classifier.

Money-aggregation terms (owes, largest invoice/quotation, average invoice value)
are general money-question terms, not eval-fitted. This regex runs on the live
/api/ask path (ask_service), so it is keyword-brittle and can over-escalate real
traffic outside the eval (a cost concern, not a correctness one).
"""

import re

_HARD_FINANCIAL_RE = re.compile(
    r"\b(outstanding|unpaid|overdue|aging|ageing|receivable|receivables|owed|owing"
    r"|past[\s-]?due|arrears|overpaid|fully[\s-]?paid|partial(?:ly)?[\s-]?paid"
    r"|owes?|largest (?:pending )?(?:invoice|quotation)|average invoice|invoice value)\b",
    re.IGNORECASE,
)

# Frozen grammar for CARDINALITY prompt injection. Hash of
# CARDINALITY_PATTERN_SOURCE is pinned in sql-flip-shape-date-p0-contract.json --
# do not OR-expand without a contract revision.
EXPLICIT_MULTIROW_RE = re.compile(
    r"\b(?:list|show\s+all|show\s+every|all\s+of|every)\b|\btop\s+\d+\b",
    re.IGNORECASE,
)
CARDINALITY_INTENT_RE = re.compile(
    r"\b(?:"
    r"top(?!\s+\d+)|largest|biggest|highest|"
    r"how\s+many|number\s+of|count(?:\s+of)?|"
    r"which\s+(?:customer|invoice|quotation|quote|order|job|product)"
    r")\b",
    re.IGNORECASE,
)
CARDINALITY_PATTERN_SOURCE = (
    f"exclude:{EXPLICIT_MULTIROW_RE.pattern}|include:{CARDINALITY_INTENT_RE.pattern}"
)


def is_hard_financial(question: str) -> bool:
    """True if the question matches the hard receivables-aggregation pattern."""
    return bool(_HARD_FINANCIAL_RE.search(question or ""))


def needs_cardinality_rules(question: str) -> bool:
    """True when the question implies singular or COUNT grain (not an explicit list/top-N).

    Oracle for tests is a hand-labeled fixture table — never this function itself.
    """
    q = question or ""
    if EXPLICIT_MULTIROW_RE.search(q):
        return False
    return bool(CARDINALITY_INTENT_RE.search(q))


def select_chat_deployment(question: str) -> str:
    """Pick the Azure chat deployment for a question via RoutePolicy."""
    from app.providers.model_purpose import ModelPurpose
    from app.providers.route_policy import RouteContext, get_route_policy

    return (
        get_route_policy()
        .resolve(ModelPurpose.sql_agent, RouteContext(question=question))
        .deployment
    )
