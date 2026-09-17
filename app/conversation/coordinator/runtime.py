"""Coordinator runtime execution and terminal outcomes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.business_query.wire.ask_result import AskBusinessQueryResult
from app.conversation.coordinator.action_lifecycle import ActionLifecycle, ActionOutcome
from app.conversation.coordinator.contracts import (
    CoordinatorContext,
    FinishAnswer,
    Observation,
    StopReason,
)
from app.conversation.coordinator.policy import CoordinatorPolicy
from app.core.turn_budget import TurnBudget

if TYPE_CHECKING:
    from app.conversation.coordinator.graph import CoordinatorGraphState
    from app.conversation.coordinator.model import CoordinatorModel
    from app.services.coordinator_tools import CoordinatorTools

EXPLANATION_UNAVAILABLE_MESSAGE = (
    "I can't explain that earlier answer any more. Ask the question again for current figures."
)


@dataclass(frozen=True)
class CoordinatorState:
    """Request-scoped counters the graph threads between nodes; private to this package."""

    decisions: int = 0
    business_queries: int = 0
    document_searches: int = 0
    restores: int = 0
    explained: bool = False
    allowed_actions: frozenset[str] = frozenset(
        {"query_business", "search_documents", "explain_sources", "finish_answer", "clarify"}
    )
    evidence_ids: tuple[str, ...] = ()
    stop_reason: StopReason | None = None


@dataclass(frozen=True)
class FinishedDraft:
    draft: FinishAnswer
    observations: tuple[Observation, ...]
    outcomes: tuple[ActionOutcome, ...]
    answer_mode: Literal["explanation", "direct"] | None


@dataclass(frozen=True)
class ClarifyRequested:
    question: str
    outcomes: tuple[ActionOutcome, ...]


@dataclass(frozen=True)
class BusinessQueryTerminal:
    """A BQ clarification, or a non-answer when the turn holds no other evidence,
    ends the turn through its existing structured contract."""

    result: AskBusinessQueryResult
    outcomes: tuple[ActionOutcome, ...]


def business_result_ends_turn(
    result: AskBusinessQueryResult, outcomes: Sequence[ActionOutcome]
) -> bool:
    """A clarification always pauses the turn: the structured route mints the
    ticket and choices from it. Any other non-answer is a tool result the model
    reads beside the evidence it already has; it ends the turn only when there
    is no such evidence."""
    if result.disposition == "clarification_required":
        return True
    return not any(o.status == "succeeded" for o in outcomes)


@dataclass(frozen=True)
class Stopped:
    reason: StopReason
    observations: tuple[Observation, ...]
    outcomes: tuple[ActionOutcome, ...]


CoordinatorTerminal = FinishedDraft | ClarifyRequested | BusinessQueryTerminal | Stopped


async def run_coordinator_turn(
    context: CoordinatorContext,
    *,
    model: CoordinatorModel,
    tools: CoordinatorTools,
    budget: TurnBudget,
    policy: CoordinatorPolicy = CoordinatorPolicy(),
    lifecycle: ActionLifecycle | None = None,
) -> CoordinatorTerminal:
    """Executes a coordinator turn via the private LangGraph StateGraph."""
    from app.conversation.coordinator.graph import build_coordinator_graph

    if lifecycle is None:
        lifecycle = ActionLifecycle(turn_id=context.turn_id)

    initial_state: CoordinatorGraphState = {
        "context": context,
        "model": model,
        "tools": tools,
        "budget": budget,
        "policy": policy,
        "lifecycle": lifecycle,
        "decisions": 0,
        "actions": 0,
        "business_queries": 0,
        "document_searches": 0,
        "restores": 0,
        "explained": False,
        "allowed_actions": frozenset(
            {"query_business", "search_documents", "explain_sources", "finish_answer", "clarify"}
        ),
        "observations": [],
        "last_action": None,
        "next_node": "decide",
        "stop_reason": None,
        "terminal": None,
    }

    compiled = build_coordinator_graph().compile()
    final_state = await compiled.ainvoke(
        initial_state,
        config={"recursion_limit": 25},
    )

    terminal = final_state.get("terminal")
    if terminal is None:
        terminal = Stopped(
            reason=final_state.get("stop_reason") or "decision_limit",
            observations=tuple(final_state.get("observations", ())),
            outcomes=lifecycle.outcomes,
        )
    return terminal
