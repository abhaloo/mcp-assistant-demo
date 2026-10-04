"""Ceilings and limits for conversational coordinator execution."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.conversation.coordinator.action_lifecycle import ActionOutcome
from app.conversation.coordinator.contracts import Observation

_EXPLANATION = re.compile(
    r"\b(why|explain|how come|what does .+ mean|compare)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CoordinatorPolicy:
    """Proposed first-release ceilings (design §4); validated at startup, frozen for evaluation."""

    max_decisions: int = 6
    max_actions: int = 4
    max_business_queries: int = 1
    max_document_searches: int = 2
    max_restores: int = 1
    decision_ceiling_seconds: float = 10.0
    final_decision_ceiling_seconds: float = 15.0


def scaled_coordinator_policy() -> CoordinatorPolicy:
    """Ask-turn decision ceilings, scaled by the budget multiplier."""
    from app.core.ask_budget import scale_ask_seconds

    base = CoordinatorPolicy()
    return CoordinatorPolicy(
        max_decisions=base.max_decisions,
        max_actions=base.max_actions,
        max_business_queries=base.max_business_queries,
        max_document_searches=base.max_document_searches,
        max_restores=base.max_restores,
        decision_ceiling_seconds=scale_ask_seconds(base.decision_ceiling_seconds),
        final_decision_ceiling_seconds=scale_ask_seconds(base.final_decision_ceiling_seconds),
    )


ALL_ACTIONS: frozenset[str] = frozenset(
    {"query_business", "search_documents", "explain_sources", "finish_answer", "clarify"}
)
ALL_ENDINGS: frozenset[str] = frozenset({"finish_answer", "clarify"})


@dataclass(frozen=True)
class TurnCounters:
    """What the turn has used so far; the graph threads these between nodes."""

    actions: int = 0
    decisions: int = 0
    business_queries: int = 0
    document_searches: int = 0
    restores: int = 0
    explained: bool = False
    business_query_failed: bool = False


TOOL_ACTIONS: frozenset[str] = frozenset({"query_business", "search_documents", "explain_sources"})


def business_query_failed(outcomes: Sequence[ActionOutcome]) -> bool:
    """True when a query_business action ran in this turn and did not succeed."""
    return any(o.kind == "query_business" and o.status != "succeeded" for o in outcomes)


def allowed_actions(
    policy: CoordinatorPolicy,
    used: TurnCounters,
    *,
    has_candidates: bool,
    available: frozenset[str] = TOOL_ACTIONS,
) -> frozenset[str]:
    """The actions the model may choose on its next decision.

    When the action or decision ceiling is reached, only finishing or
    clarifying remain (or finish_answer alone if a query failed). A turn
    whose query ran is not offered clarify: it finishes from the answer
    or the failure. Explaining restores an earlier answer, so it is open
    until the restore cap is reached and only when the conversation holds an
    answer to explain. A tool the request cannot run (`available`) is never
    offered.
    """
    queried = used.business_query_failed or used.business_queries > 0
    if used.actions >= policy.max_actions or used.decisions >= policy.max_decisions - 1:
        if queried:
            return frozenset({"finish_answer"})
        return ALL_ENDINGS
    allowed = set(ALL_ACTIONS) - (TOOL_ACTIONS - available)
    if used.business_queries >= policy.max_business_queries:
        allowed.discard("query_business")
    if used.document_searches >= policy.max_document_searches:
        allowed.discard("search_documents")
    if used.restores >= policy.max_restores or not has_candidates:
        allowed.discard("explain_sources")
    if queried:
        allowed.discard("clarify")
    return frozenset(allowed)


def finishes_without_narration(
    *,
    outcomes: Sequence[ActionOutcome],
    observations: Sequence[Observation],
    question: str,
    compound: bool = False,
) -> bool:
    """A single table answer with no document evidence and no request for an
    explanation is finished by the presenter's text; a second model pass would
    only repeat the table. The presenter's text describes the first table only,
    so an answer with more tables is narrated."""
    if compound:
        return False
    if (
        len(outcomes) != 1
        or outcomes[0].kind != "query_business"
        or outcomes[0].status != "succeeded"
    ):
        return False
    if not observations or not observations[-1].rows or len(observations[-1].tables) > 1:
        return False
    return _EXPLANATION.search(question) is None
