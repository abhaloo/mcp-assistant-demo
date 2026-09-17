"""Ceilings and limits for conversational coordinator execution."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CoordinatorPolicy:
    """Proposed first-release ceilings (design §4); validated at startup, frozen for evaluation."""

    max_decisions: int = 4
    max_actions: int = 3
    max_business_queries: int = 1
    max_document_searches: int = 2
    max_restores: int = 1
    decision_ceiling_seconds: float = 4.0
    final_decision_ceiling_seconds: float = 6.0


ALL_ACTIONS: frozenset[str] = frozenset(
    {"query_business", "search_documents", "explain_sources", "finish_answer", "clarify"}
)


@dataclass(frozen=True)
class TurnCounters:
    """What the turn has used so far; the graph threads these between nodes."""

    actions: int = 0
    decisions: int = 0
    business_queries: int = 0
    document_searches: int = 0
    restores: int = 0
    explained: bool = False


TOOL_ACTIONS: frozenset[str] = frozenset({"query_business", "search_documents", "explain_sources"})


def allowed_actions(
    policy: CoordinatorPolicy,
    used: TurnCounters,
    *,
    has_candidates: bool,
    available: frozenset[str] = TOOL_ACTIONS,
) -> frozenset[str]:
    """The actions the model may choose on its next decision.

    Once an explanation ran, or the action or decision ceiling is one step
    away, only finishing or clarifying remain. Explaining restores an earlier
    answer, so it is open only as the first action of a turn and only when the
    conversation holds an answer to explain. A tool the request cannot run
    (`available`) is never offered.
    """
    if (
        used.explained
        or used.actions >= policy.max_actions
        or used.decisions >= policy.max_decisions - 1
    ):
        return frozenset({"finish_answer", "clarify"})
    allowed = set(ALL_ACTIONS) - (TOOL_ACTIONS - available)
    if used.business_queries >= policy.max_business_queries:
        allowed.discard("query_business")
    if used.document_searches >= policy.max_document_searches:
        allowed.discard("search_documents")
    if used.actions > 0 or used.restores >= policy.max_restores or not has_candidates:
        allowed.discard("explain_sources")
    return frozenset(allowed)
