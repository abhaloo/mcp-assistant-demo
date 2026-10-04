"""LangGraph private state graph for the conversational coordinator loop."""

from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.business_query.wire.answer_head import answer_head
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.conversation.coordinator.action_lifecycle import (
    ActionLifecycle,
    ActionOutcome,
    ActionProtocolError,
)
from app.conversation.coordinator.context_budget import ContextBudgetExceeded
from app.conversation.coordinator.contracts import (
    REPAIR_PREFIX,
    ActionKind,
    AnswerBlock,
    BusinessQuestion,
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
from app.conversation.coordinator.draft_checks import (
    CLARIFY_RULES,
    FINISH_RULES,
    ActionT,
    DraftFacts,
    DraftRule,
)
from app.conversation.coordinator.model import CoordinatorModel, MalformedDecisionError
from app.conversation.coordinator.observations import project_observations
from app.conversation.coordinator.policy import (
    CoordinatorPolicy,
    TurnCounters,
    allowed_actions,
    business_query_failed,
    finishes_without_narration,
)
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
    # Names of the draft rules whose one repair round this turn has used.
    repairs_used: frozenset[str]
    held_draft: FinishedDraft | None
    explained: bool

    allowed_actions: frozenset[str]
    observations: list[Observation]
    last_action: CoordinatorAction | None
    next_node: str
    # Set when the graph itself drafted the answer from a table's head.
    draft_text_kind: Literal["narrative", "table_fallback"]

    stop_reason: StopReason | None
    terminal: CoordinatorTerminal | None


def _answer_text(result: CommittedBqResult | AskBusinessQueryResult) -> str:
    payload = result.result if isinstance(result, CommittedBqResult) else result
    return payload.answer_text


def _question_origin_from_bq_result(
    result: CommittedBqResult | AskBusinessQueryResult,
) -> QuestionOrigin:
    """Origin follows the receipt tier; no envelope means the person wrote it."""
    payload = result.result if isinstance(result, CommittedBqResult) else result
    wire = getattr(payload, "business_query", None)
    if wire is None:
        return "user"
    envelopes = list(wire.envelopes) if wire.envelopes else []
    if not envelopes and wire.envelope is not None:
        envelopes = [wire.envelope]
    if not envelopes:
        return "user"
    receipt = envelopes[0].receipt
    return "model" if receipt.continuation_tier == "patch" else "user"


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


def _stopped_step(
    reason: StopReason, observations: list[Observation], lifecycle: Any
) -> dict[str, Any]:
    terminal = Stopped(
        reason=reason,
        observations=tuple(observations),
        outcomes=lifecycle.outcomes,
    )
    return {"terminal": terminal, "next_node": END}


def _repair_observation(tag: str | None, decisions: int, text: str) -> Observation:
    """The observation that sends the model back with what to fix."""
    return Observation(
        action_id=f"repair-{tag}-{decisions}" if tag else f"repair-{decisions}",
        kind="decision_invalid",
        status="failed",
        summary=f"{REPAIR_PREFIX} {text}",
    )


def _first_repair(  # noqa: PLR0913 - the state set, the action, its rule table and its facts
    state: CoordinatorGraphState,
    action: ActionT,
    rules: tuple[DraftRule[ActionT], ...],
    facts: DraftFacts,
    *,
    tag: str,
    held: FinishedDraft | None,
) -> dict[str, Any] | None:
    """The update for the first rule the action breaks; None when it keeps them all."""
    used = state.get("repairs_used", frozenset())
    for rule in rules:
        text = rule.check(action, facts)
        if text is None:
            continue
        if rule.name in used:
            if rule.stops_when_repeated:
                return _stopped_step(
                    "malformed_response", list(facts.observations), state["lifecycle"]
                )
            continue
        repair = _repair_observation(tag, state.get("decisions", 0), text)
        update: dict[str, Any] = {
            "observations": [*facts.observations, repair],
            "repairs_used": used | {rule.name},
            "next_node": "decide",
        }
        if rule.holds_draft:
            update["held_draft"] = held
        return update
    return None


def _draft_facts(state: CoordinatorGraphState, lifecycle: ActionLifecycle) -> DraftFacts:
    context = state.get("context")
    return DraftFacts(
        observations=tuple(state.get("observations", [])),
        outcomes=lifecycle.outcomes,
        earlier=frozenset(h.exchange_id for h in context.history) if context else frozenset(),
        allowed=_next_allowed(state, lifecycle),
        unreachable=context.unreachable if context else (),
    )


def _pre_decision_stop(
    lifecycle: Any, budget: TurnBudget, *, decisions: int, max_decisions: int
) -> StopReason | None:
    """The reason the turn stops before the model is asked, if any."""
    try:
        lifecycle.assert_ready_for_model()
    except ActionProtocolError:
        return "action_protocol_error"
    try:
        budget.check_not_expired()
    except DeadlineExpiredError:
        return "budget_expired"
    if budget.remaining_seconds <= 0:
        return "budget_expired"
    if decisions >= max_decisions:
        return "decision_limit"
    return None


def _disallowed_reason(
    action_kind: str, counters: TurnCounters, policy: CoordinatorPolicy
) -> StopReason:
    if action_kind == "query_business" and counters.business_queries >= policy.max_business_queries:
        return "business_query_limit"
    if (
        action_kind == "search_documents"
        and counters.document_searches >= policy.max_document_searches
    ):
        return "document_search_limit"
    if (
        action_kind in ("query_business", "search_documents", "explain_sources")
        and counters.actions >= policy.max_actions
    ):
        return "action_limit"
    return "action_not_allowed"


def _turn_counters(state: CoordinatorGraphState, lifecycle: ActionLifecycle) -> TurnCounters:
    return TurnCounters(
        actions=state.get("actions", 0),
        decisions=state.get("decisions", 0),
        business_queries=state.get("business_queries", 0),
        document_searches=state.get("document_searches", 0),
        restores=state.get("restores", 0),
        explained=state.get("explained", False),
        business_query_failed=business_query_failed(lifecycle.outcomes),
    )


def _allowed_next(
    counters: TurnCounters,
    *,
    policy: CoordinatorPolicy,
    context: CoordinatorContext,
    tools: CoordinatorTools,
) -> frozenset[str]:
    """The actions the model may choose on its next decision."""
    return allowed_actions(
        policy,
        counters,
        has_candidates=bool(context.candidates),
        available=frozenset(tools.available_actions()),
    )


def _next_allowed(state: CoordinatorGraphState, lifecycle: ActionLifecycle) -> frozenset[str]:
    """The actions the next decision offers; none when the state cannot say."""
    policy, context, tools = state.get("policy"), state.get("context"), state.get("tools")
    if policy is None or context is None or tools is None:
        return frozenset()
    counters = _turn_counters(state, lifecycle)
    return _allowed_next(counters, policy=policy, context=context, tools=tools)


async def decide_node(state: CoordinatorGraphState) -> dict[str, Any]:
    lifecycle = state["lifecycle"]
    observations = list(state.get("observations", []))
    budget = state["budget"]
    policy = state["policy"]
    context = state["context"]
    decisions = state.get("decisions", 0)

    stop = _pre_decision_stop(
        lifecycle, budget, decisions=decisions, max_decisions=policy.max_decisions
    )
    if stop is not None:
        return _stopped_step(stop, observations, lifecycle)

    counters = _turn_counters(state, lifecycle)
    allowed = _allowed_next(counters, policy=policy, context=context, tools=state["tools"])

    model = state["model"]
    current_observations = tuple(observations)

    for attempt in range(2):
        try:
            action = await await_with_budget(
                lambda obs=current_observations: model.decide(
                    context,
                    obs,
                    allowed_actions=allowed,
                    budget=budget,
                ),
                budget,
            )
            break
        except MalformedDecisionError as exc:
            # Only the closed failure kind and tool name reach the log; the
            # message may carry the model's argument text.
            logger.warning(
                "coordinator decision malformed: kind=%s tool=%s raw=%s decisions=%s",
                exc.kind,
                exc.tool,
                exc.redacted_text,
                decisions,
            )
            if attempt == 0:
                repair_obs = _repair_observation(
                    None,
                    decisions,
                    exc.kind + (f" for {exc.tool}" if exc.tool else ""),
                )
                current_observations = (*observations, repair_obs)
                continue
            return _stopped_step("malformed_response", observations, lifecycle)
        except DeadlineExpiredError:
            return _stopped_step("budget_expired", observations, lifecycle)
        except ContextBudgetExceeded:
            return _stopped_step("context_budget_exceeded", observations, lifecycle)

    if action.kind not in allowed:
        return _stopped_step(
            _disallowed_reason(action.kind, counters, policy), observations, lifecycle
        )

    return {
        "decisions": decisions + 1,
        "allowed_actions": allowed,
        "last_action": action,
        "next_node": action.kind,
    }


def _published_draft(
    state: CoordinatorGraphState,
    action: FinishAnswer,
    outcomes: tuple[ActionOutcome, ...],
) -> FinishedDraft:
    if len(outcomes) == 0:
        answer_mode = "direct"
    elif all(o.kind == "explain_sources" for o in outcomes) and len(outcomes) > 0:
        answer_mode = "explanation"
    else:
        answer_mode = None

    return FinishedDraft(
        draft=action,
        observations=tuple(state.get("observations", [])),
        outcomes=outcomes,
        answer_mode=answer_mode,
        text_kind=state.get("draft_text_kind", "narrative"),
    )


async def finish_node(state: CoordinatorGraphState) -> dict[str, Any]:
    action = state["last_action"]
    assert isinstance(action, FinishAnswer)
    lifecycle = state["lifecycle"]
    terminal = _published_draft(state, action, lifecycle.outcomes)
    repair = _first_repair(
        state, action, FINISH_RULES, _draft_facts(state, lifecycle), tag="draft", held=terminal
    )
    return repair or {"terminal": terminal, "next_node": END}


async def clarify_node(state: CoordinatorGraphState) -> dict[str, Any]:
    action = state["last_action"]
    assert isinstance(action, Clarify)
    lifecycle = state["lifecycle"]
    repair = _first_repair(
        state, action, CLARIFY_RULES, _draft_facts(state, lifecycle), tag="clarify", held=None
    )
    if repair is not None:
        return repair
    terminal = ClarifyRequested(
        question=action.question, choices=action.choices, outcomes=lifecycle.outcomes
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
    # A part document search already answered is not the planner's to plan.
    documents_answered = any(
        o.kind == "search_documents" and o.status == "succeeded" and o.evidence_ids
        for o in observations
    )
    question = BusinessQuestion(
        raw=action.question if documents_answered else state["context"].question,
        reading=action.question,
        binding=action.binding,
    )

    try:
        result = await await_with_budget(
            lambda: state["tools"].query_business(question, continues=action.continues),
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

    question_origin = _question_origin_from_bq_result(result)
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
        if finishes_without_narration(
            outcomes=lifecycle.outcomes,
            observations=observations,
            question=state["context"].question,
            compound=action.compound,
        ):
            payload = result.result
            wire = payload.business_query
            envelopes = list(wire.envelopes) if wire and wire.envelopes else []
            if not envelopes and wire and wire.envelope is not None:
                envelopes = [wire.envelope]
            if envelopes:
                first = envelopes[0]
                plan = payload.plan
                text = answer_head(
                    plan=plan,
                    row_identity=first.row_identity,
                    shown=len(first.rows),
                    total=first.total_row_count,
                    changes=first.receipt.changes,
                )
            else:
                text = _answer_text(result)
            draft = FinishAnswer(
                blocks=(
                    AnswerBlock(
                        text=text,
                        claim_type="evidence",
                        evidence_ids=(action_id,),
                    ),
                ),
            )
            return {
                "actions": state.get("actions", 0) + 1,
                "business_queries": state.get("business_queries", 0) + 1,
                "observations": observations,
                "last_action": draft,
                "draft_text_kind": "table_fallback",
                "next_node": "finish",
            }
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
    if next_node == "finish":
        return "finish"
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
    graph.add_conditional_edges("finish", route_after_action, {"decide": "decide", END: END})
    graph.add_conditional_edges("clarify", route_after_action, {"decide": "decide", END: END})
    graph.add_edge("stop", END)

    graph.add_conditional_edges(
        "query_business",
        route_after_action,
        {"decide": "decide", "finish": "finish", END: END},
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
