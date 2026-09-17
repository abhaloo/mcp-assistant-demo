"""LangGraph private state graph for the conversational coordinator loop."""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.business_query.wire.ask_result import CommittedBqResult
from app.conversation.coordinator.action_lifecycle import (
    ActionLifecycle,
    ActionOutcome,
    ActionProtocolError,
)
from app.conversation.coordinator.context_budget import ContextBudgetExceeded
from app.conversation.coordinator.contracts import (
    ActionKind,
    Clarify,
    CoordinatorAction,
    CoordinatorContext,
    ExplainSources,
    FinishAnswer,
    Observation,
    QueryBusiness,
    QuestionOrigin,
    SearchDocuments,
    StopReason,
)
from app.conversation.coordinator.model import CoordinatorModel, MalformedDecisionError
from app.conversation.coordinator.observations import project_observations
from app.conversation.coordinator.policy import CoordinatorPolicy, TurnCounters, allowed_actions
from app.conversation.coordinator.runtime import (
    BusinessQueryTerminal,
    ClarifyRequested,
    CoordinatorTerminal,
    FinishedDraft,
    Stopped,
    business_result_ends_turn,
)
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import TurnBudget, await_with_budget
from app.rag.retrieval.document_contracts import DocumentSearchResult
from app.services.coordinator_tools import CoordinatorTools

logger = logging.getLogger(__name__)


class CoordinatorGraphState(TypedDict, total=False):
    context: CoordinatorContext
    model: CoordinatorModel
    tools: CoordinatorTools
    budget: TurnBudget
    policy: CoordinatorPolicy
    lifecycle: ActionLifecycle

    decisions: int
    actions: int
    business_queries: int
    document_searches: int
    restores: int
    explained: bool

    allowed_actions: frozenset[str]
    observations: list[Observation]
    last_action: CoordinatorAction | None
    next_node: str

    stop_reason: StopReason | None
    terminal: CoordinatorTerminal | None


def _observe(
    outcome: ActionOutcome, *, question_origin: QuestionOrigin | None = None
) -> Observation:
    """The business-safe projection of one outcome is what the model reads:
    passage text, restored answers and formatted values, never raw SQL."""
    obs = project_observations((outcome,))[0]
    return (
        obs
        if question_origin is None
        else obs.model_copy(update={"question_origin": question_origin})
    )


def _failed_action(
    state: CoordinatorGraphState,
    *,
    action_id: str,
    kind: ActionKind,
    counter: str,
    error: Exception,
) -> dict[str, Any]:
    """A tool that raised is a result the model reads, not the end of the
    turn. The observation carries the failure class only; the exception text
    stays in the log at this seam."""
    logger.warning(
        "coordinator tool failed action_id=%s kind=%s error=%s",
        action_id,
        kind,
        type(error).__name__,
    )
    lifecycle = state["lifecycle"]
    outcome = ActionOutcome(
        turn_id=state["context"].turn_id,
        action_id=action_id,
        kind=kind,
        status="failed",
        result=error,
    )
    lifecycle.complete(action_id, outcome)
    observations = [*state.get("observations", []), _observe(outcome)]
    return {
        "actions": state.get("actions", 0) + 1,
        counter: state.get(counter, 0) + 1,
        "observations": observations,
        "next_node": "decide",
    }


async def decide_node(state: CoordinatorGraphState) -> dict[str, Any]:
    lifecycle = state["lifecycle"]
    observations = list(state.get("observations", []))

    try:
        lifecycle.assert_ready_for_model()
    except ActionProtocolError:
        terminal = Stopped(
            reason="action_protocol_error",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    budget = state["budget"]
    try:
        budget.check_not_expired()
        if budget.remaining_seconds <= 0:
            raise DeadlineExpiredError("budget expired")
    except DeadlineExpiredError:
        terminal = Stopped(
            reason="budget_expired",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    policy = state["policy"]
    decisions = state.get("decisions", 0)
    if decisions >= policy.max_decisions:
        terminal = Stopped(
            reason="decision_limit",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    actions = state.get("actions", 0)
    business_queries = state.get("business_queries", 0)
    document_searches = state.get("document_searches", 0)
    restores = state.get("restores", 0)
    explained = state.get("explained", False)

    allowed = allowed_actions(
        policy,
        TurnCounters(
            actions=actions,
            decisions=decisions,
            business_queries=business_queries,
            document_searches=document_searches,
            restores=restores,
            explained=explained,
        ),
        has_candidates=bool(state["context"].candidates),
        available=frozenset(state["tools"].available_actions()),
    )

    model = state["model"]
    context = state["context"]
    try:
        action = await await_with_budget(
            lambda: model.decide(
                context,
                tuple(observations),
                allowed_actions=allowed,
                budget=budget,
            ),
            budget,
        )
    except MalformedDecisionError as exc:
        # Only the closed failure kind and tool name reach the log; the
        # message may carry the model's argument text.
        logger.warning(
            "coordinator decision malformed: kind=%s tool=%s raw=%s decisions=%s",
            exc.kind,
            exc.tool,
            exc.redacted_text,
            state.get("decisions", 0),
        )
        terminal = Stopped(
            reason="malformed_response",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}
    except DeadlineExpiredError:
        terminal = Stopped(
            reason="budget_expired",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}
    except ContextBudgetExceeded:
        terminal = Stopped(
            reason="context_budget_exceeded",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    if action.kind not in allowed:
        if action.kind == "query_business" and business_queries >= policy.max_business_queries:
            reason: StopReason = "business_query_limit"
        elif (
            action.kind == "search_documents" and document_searches >= policy.max_document_searches
        ):
            reason = "document_search_limit"
        elif (
            action.kind in ("query_business", "search_documents", "explain_sources")
            and actions >= policy.max_actions
        ):
            reason = "action_limit"
        else:
            reason = "action_not_allowed"

        terminal = Stopped(
            reason=reason,
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    return {
        "decisions": decisions + 1,
        "allowed_actions": allowed,
        "last_action": action,
        "next_node": action.kind,
    }


async def finish_node(state: CoordinatorGraphState) -> dict[str, Any]:
    action = state["last_action"]
    assert isinstance(action, FinishAnswer)
    lifecycle = state["lifecycle"]
    outcomes = lifecycle.outcomes
    if len(outcomes) == 0:
        answer_mode = "direct"
    elif all(o.kind == "explain_sources" for o in outcomes) and len(outcomes) > 0:
        answer_mode = "explanation"
    else:
        answer_mode = None

    terminal = FinishedDraft(
        draft=action,
        observations=tuple(state.get("observations", [])),
        outcomes=outcomes,
        answer_mode=answer_mode,
    )
    return {"terminal": terminal, "next_node": END}


async def clarify_node(state: CoordinatorGraphState) -> dict[str, Any]:
    action = state["last_action"]
    assert isinstance(action, Clarify)
    lifecycle = state["lifecycle"]
    terminal = ClarifyRequested(
        question=action.question,
        outcomes=lifecycle.outcomes,
    )
    return {"terminal": terminal, "next_node": END}


async def stop_node(state: CoordinatorGraphState) -> dict[str, Any]:
    return {"terminal": state.get("terminal"), "next_node": END}


async def query_business_node(state: CoordinatorGraphState) -> dict[str, Any]:
    action = state["last_action"]
    assert isinstance(action, QueryBusiness)
    lifecycle = state["lifecycle"]
    action_id = f"act-{state.get('actions', 0) + 1}"

    try:
        lifecycle.admit(action_id, "query_business")
        lifecycle.start(action_id)
    except ActionProtocolError:
        terminal = Stopped(
            reason="action_protocol_error",
            observations=tuple(state.get("observations", [])),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    observations = list(state.get("observations", []))
    first_query = len(observations) == 0
    question_to_use = state["context"].question if first_query else action.question
    question_origin = "user" if first_query else "model"

    try:
        result = await await_with_budget(
            lambda: state["tools"].query_business(question_to_use),
            state["budget"],
        )
    except DeadlineExpiredError:
        outcome = ActionOutcome(
            turn_id=state["context"].turn_id,
            action_id=action_id,
            kind="query_business",
            status="timed_out",
            result=None,
        )
        lifecycle.complete(action_id, outcome)
        terminal = Stopped(
            reason="budget_expired",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}
    except Exception as error:  # noqa: BLE001 - any tool fault is a failed action the model reads
        return _failed_action(
            state,
            action_id=action_id,
            kind="query_business",
            counter="business_queries",
            error=error,
        )

    if isinstance(result, CommittedBqResult):
        outcome = ActionOutcome(
            turn_id=state["context"].turn_id,
            action_id=action_id,
            kind="query_business",
            status="succeeded",
            result=result,
        )
        lifecycle.complete(action_id, outcome)
        observations.append(_observe(outcome, question_origin=question_origin))
        return {
            "actions": state.get("actions", 0) + 1,
            "business_queries": state.get("business_queries", 0) + 1,
            "observations": observations,
            "next_node": "decide",
        }

    status = (
        "denied"
        if result.disposition == "denied"
        else ("timed_out" if result.sql_stop_reason == "timeout" else "failed")
    )
    outcome = ActionOutcome(
        turn_id=state["context"].turn_id,
        action_id=action_id,
        kind="query_business",
        status=status,
        result=result,
    )
    lifecycle.complete(action_id, outcome)
    if business_result_ends_turn(result, lifecycle.outcomes):
        terminal = BusinessQueryTerminal(
            result=result,
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}
    observations.append(_observe(outcome, question_origin=question_origin))
    return {
        "actions": state.get("actions", 0) + 1,
        "business_queries": state.get("business_queries", 0) + 1,
        "observations": observations,
        "next_node": "decide",
    }


async def search_documents_node(state: CoordinatorGraphState) -> dict[str, Any]:
    action = state["last_action"]
    assert isinstance(action, SearchDocuments)
    lifecycle = state["lifecycle"]
    action_id = f"act-{state.get('actions', 0) + 1}"

    try:
        lifecycle.admit(action_id, "search_documents")
        lifecycle.start(action_id)
    except ActionProtocolError:
        terminal = Stopped(
            reason="action_protocol_error",
            observations=tuple(state.get("observations", [])),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    observations = list(state.get("observations", []))
    try:
        result = await await_with_budget(
            lambda: state["tools"].search_documents(action.question),
            state["budget"],
        )
    except DeadlineExpiredError:
        outcome = ActionOutcome(
            turn_id=state["context"].turn_id,
            action_id=action_id,
            kind="search_documents",
            status="timed_out",
            result=None,
        )
        lifecycle.complete(action_id, outcome)
        terminal = Stopped(
            reason="budget_expired",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}
    except Exception as error:  # noqa: BLE001 - any tool fault is a failed action the model reads
        return _failed_action(
            state,
            action_id=action_id,
            kind="search_documents",
            counter="document_searches",
            error=error,
        )

    status = (
        "succeeded"
        if isinstance(result, DocumentSearchResult)
        else ("timed_out" if getattr(result, "status", None) == "timeout" else "failed")
    )
    outcome = ActionOutcome(
        turn_id=state["context"].turn_id,
        action_id=action_id,
        kind="search_documents",
        status=status,
        result=result,
    )
    lifecycle.complete(action_id, outcome)
    observations.append(_observe(outcome))
    return {
        "actions": state.get("actions", 0) + 1,
        "document_searches": state.get("document_searches", 0) + 1,
        "observations": observations,
        "next_node": "decide",
    }


async def explain_sources_node(state: CoordinatorGraphState) -> dict[str, Any]:
    action = state["last_action"]
    assert isinstance(action, ExplainSources)
    lifecycle = state["lifecycle"]
    action_id = f"act-{state.get('actions', 0) + 1}"

    try:
        lifecycle.admit(action_id, "explain_sources")
        lifecycle.start(action_id)
    except ActionProtocolError:
        terminal = Stopped(
            reason="action_protocol_error",
            observations=tuple(state.get("observations", [])),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    observations = list(state.get("observations", []))
    try:
        result = await await_with_budget(
            lambda: state["tools"].explain_sources(action.selection),
            state["budget"],
        )
    except DeadlineExpiredError:
        outcome = ActionOutcome(
            turn_id=state["context"].turn_id,
            action_id=action_id,
            kind="explain_sources",
            status="timed_out",
            result=None,
        )
        lifecycle.complete(action_id, outcome)
        terminal = Stopped(
            reason="budget_expired",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}
    except Exception as error:  # noqa: BLE001 - any tool fault is a failed action the model reads
        return _failed_action(
            state,
            action_id=action_id,
            kind="explain_sources",
            counter="restores",
            error=error,
        )

    if result is None:
        outcome = ActionOutcome(
            turn_id=state["context"].turn_id,
            action_id=action_id,
            kind="explain_sources",
            status="failed",
            result=None,
        )
        lifecycle.complete(action_id, outcome)
        terminal = Stopped(
            reason="explanation_unavailable",
            observations=tuple(observations),
            outcomes=lifecycle.outcomes,
        )
        return {"terminal": terminal, "next_node": END}

    outcome = ActionOutcome(
        turn_id=state["context"].turn_id,
        action_id=action_id,
        kind="explain_sources",
        status="succeeded",
        result=result,
    )
    lifecycle.complete(action_id, outcome)
    observations.append(_observe(outcome))
    return {
        "actions": state.get("actions", 0) + 1,
        "restores": state.get("restores", 0) + 1,
        "explained": True,
        "observations": observations,
        "next_node": "decide",
    }


def route_after_decide(state: CoordinatorGraphState) -> str:
    next_node = state.get("next_node")
    if next_node == "finish_answer":
        return "finish"
    if next_node == "clarify":
        return "clarify"
    if next_node in ("query_business", "search_documents", "explain_sources"):
        return next_node
    return END


def route_after_action(state: CoordinatorGraphState) -> str:
    next_node = state.get("next_node")
    if next_node == "decide":
        return "decide"
    return END


def build_coordinator_graph() -> StateGraph:
    graph = StateGraph(CoordinatorGraphState)
    graph.add_node("decide", decide_node)
    graph.add_node("finish", finish_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("stop", stop_node)
    graph.add_node("query_business", query_business_node)
    graph.add_node("search_documents", search_documents_node)
    graph.add_node("explain_sources", explain_sources_node)

    graph.add_edge(START, "decide")
    graph.add_conditional_edges(
        "decide",
        route_after_decide,
        {
            "finish": "finish",
            "clarify": "clarify",
            "query_business": "query_business",
            "search_documents": "search_documents",
            "explain_sources": "explain_sources",
            END: END,
        },
    )
    graph.add_edge("finish", END)
    graph.add_edge("clarify", END)
    graph.add_edge("stop", END)

    graph.add_conditional_edges(
        "query_business",
        route_after_action,
        {"decide": "decide", END: END},
    )
    graph.add_conditional_edges(
        "search_documents",
        route_after_action,
        {"decide": "decide", END: END},
    )
    graph.add_conditional_edges(
        "explain_sources",
        route_after_action,
        {"decide": "decide", END: END},
    )
    return graph
