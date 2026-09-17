"""Pure predicates for named-entity question shape vs plan dimensions."""

from __future__ import annotations

import re

from app.business_query.plan.query_plan import BusinessQueryPlan

_FILLERS = frozenset({"so", "ok", "please"})
_SUPERLATIVES = frozenset(
    {
        "most",
        "fewest",
        "largest",
        "smallest",
        "biggest",
        "least",
        "zaidi",
        "mkubwa",
        "kubwa",
    }
)
_WHO_CUES = frozenset({"who", "which", "nani", "gani"})
_NAME_DIMENSIONS = frozenset({"customer.name", "invoice.customer_name"})
_TOKEN = re.compile(r"[\w']+", re.UNICODE)


def _tokens(question: str) -> list[str]:
    return _TOKEN.findall(question.casefold())


def _strip_leading_fillers(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens) and tokens[index] in _FILLERS:
        index += 1
    return tokens[index:]


def wants_named_entity(question: str) -> bool:
    """True when the question pairs a who/which cue with a superlative token."""
    tokens = _strip_leading_fillers(_tokens(question))
    token_set = set(tokens)
    has_cue = bool(token_set & _WHO_CUES)
    has_superlative = bool(token_set & _SUPERLATIVES)
    return has_cue and has_superlative


def _dimension_names_entity(dimension: str) -> bool:
    return ".name" in dimension or dimension in _NAME_DIMENSIONS


def plan_names_entity(plan: BusinessQueryPlan) -> bool:
    """True when a non-scalar plan groups by a name-like entity dimension."""
    if plan.grain == "scalar":
        return False
    return any(_dimension_names_entity(dimension) for dimension in plan.dimensions)
