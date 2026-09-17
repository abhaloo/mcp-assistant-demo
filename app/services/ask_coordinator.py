"""Application service adapter connecting CoordinatorTerminal to AskOutcome."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from app.auth import Principal
from app.conversation.coordinator.action_lifecycle import ActionLifecycle
from app.conversation.coordinator.contracts import ActionKind
from app.conversation.coordinator.policy import CoordinatorPolicy
from app.conversation.coordinator.runtime import (
    EXPLANATION_UNAVAILABLE_MESSAGE,
    BusinessQueryTerminal,
    ClarifyRequested,
    FinishedDraft,
    run_coordinator_turn,
)
from app.conversation.coordinator.runtime import (
    Stopped as CoordinatorStopped,
)
from app.core.ask_errors import (
    CAPABILITY_UNAVAILABLE_MESSAGE,
    resolve_production_route,
)
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.models.citations import CitationsPayload
from app.models.schemas import QueryType, Question
from app.providers.model_purpose import ModelPurpose
from app.resources import ProcessResources
from app.services import coordinator_composition
from app.services.ask_outcome import (
    Answered,
    AskOutcome,
    CapabilityUnavailable,
    FixedMessage,
)
from app.services.ask_outcome import (
    Stopped as AskStopped,
)
from app.services.ask_v2_reason_copy import copy_for_reason
from app.services.coordinator_answer import committed_business_result, retrieved_passages
from app.services.coordinator_thoughts import TurnThoughts
from app.services.coordinator_tools import CoordinatorProgress

if TYPE_CHECKING:
    from app.conversation.coordinator.contracts import (
        CoordinatorAction,
        CoordinatorContext,
        Observation,
    )
    from app.conversation.coordinator.model import CoordinatorModel
    from app.query_records.context import TerminalUsageCapture
    from app.services.ask_prepare import PreparedCoordinatorTurn
    from app.services.coordinator_tools import CoordinatorTools

# Plain words for the thinking phase. Canned lines never name a tool; provider
# reasoning summary text passes through TurnThoughts identifier filtering.
_THOUGHT_OPENING = "Reading your question and what this conversation already holds.\n"
_DecisionKind = ActionKind | Literal["finish_answer", "clarify"]
# Keyed on the closed decision union so a new action kind fails the type
# check here instead of silently thinking nothing.
_THOUGHT_BY_ACTION: dict[_DecisionKind, str] = {
    "query_business": "Next: look up the figures in your business data.\n",
    "search_documents": "Next: search the company documents.\n",
    "explain_sources": "Next: explain the result you already have.\n",
    "finish_answer": "Ready to write the answer.\n",
    "clarify": "One detail is missing before this can be answered.\n",
}


# Plain words for each way the coordinator can stop short of an answer. The
# transient copy is the same the Business Query route uses for its budget and
# timeout stops, so the panel treats both the same way.
_STOP_MESSAGES: dict[str, str] = {
    "explanation_unavailable": EXPLANATION_UNAVAILABLE_MESSAGE,
    "decision_limit": copy_for_reason("budget"),
    "action_limit": copy_for_reason("budget"),
    "business_query_limit": copy_for_reason("budget"),
    "document_search_limit": copy_for_reason("budget"),
    "context_budget_exceeded": copy_for_reason("budget"),
    "budget_expired": copy_for_reason("timeout"),
    "malformed_response": (
        "I could not put that answer together properly. Please ask the question again."
    ),
    "action_protocol_error": (
        "I could not put that answer together properly. Please ask the question again."
    ),
    "action_not_allowed": (
        "I could not put that answer together properly. Please ask the question again."
    ),
}


class _ThinkingAloudModel:
    """Narrates each decision to the progress sink as one short line."""

    def __init__(self, inner: CoordinatorModel, progress: CoordinatorProgress) -> None:
        self._inner = inner
        self._progress = progress
        self._opened = False

    async def decide(
        self,
        context: CoordinatorContext,
        observations: tuple[Observation, ...],
        *,
        allowed_actions: frozenset[str],
        budget: TurnBudget,
    ) -> CoordinatorAction:
        self._progress.stage("thinking")
        if not self._opened:
            self._opened = True
            self._progress.emit_thought_delta(_THOUGHT_OPENING)
        action = await self._inner.decide(
            context, observations, allowed_actions=allowed_actions, budget=budget
        )
        line = _THOUGHT_BY_ACTION.get(action.kind)
        if line is not None:
            self._progress.emit_thought_delta(line)
        return action


def _answer_query_type(
    answer_mode: str | None, *, has_business: bool, has_documents: bool
) -> QueryType:
    """The wire query type is the evidence the answer stands on."""
    if answer_mode == "explanation":
        return "semantic"
    if has_business and has_documents:
        return "both"
    if has_documents:
        return "semantic"
    return "structured"


def _extract_terminal_usage(correlation_id: str | None) -> TerminalUsageCapture | None:
    if not correlation_id:
        return None
    from app.query_records.context import TerminalUsageCapture
    from app.telemetry.invocation_payload import get_recorded_evidence

    records = get_recorded_evidence(correlation_id)
    if not records:
        return None
    usage = TerminalUsageCapture()
    total_in = 0
    total_out = 0
    total_reasoning = 0
    has_tokens = False
    for r in records:
        inp = getattr(r, "input_tokens", None)
        outp = getattr(r, "output_tokens", None)
        reasoning = getattr(r, "reasoning_tokens", None)
        if inp is not None:
            total_in += inp
            has_tokens = True
        if outp is not None:
            total_out += outp
            has_tokens = True
        if reasoning is not None:
            total_reasoning += reasoning
        if getattr(r, "cost_status", None) and not usage.cost_status:
            usage.cost_status = r.cost_status
        if getattr(r, "model", None) and not usage.model:
            usage.model = r.model
        if getattr(r, "provider", None) and not usage.provider:
            usage.provider = r.provider
    if has_tokens:
        usage.input_tokens = total_in
        usage.output_tokens = total_out
        usage.reasoning_tokens = total_reasoning
    return usage


async def run_coordinator_ask(
    coord_turn: PreparedCoordinatorTurn,
    *,
    body: Question,
    principal: Principal,
    resources: ProcessResources | None = None,
    progress: object | None = None,
    disconnected: object | None = None,
    lifecycle_run_id: str | None = None,
    turn_budget: TurnBudget | None = None,
    model: CoordinatorModel | None = None,
    tools: CoordinatorTools | None = None,
    policy: CoordinatorPolicy = CoordinatorPolicy(),
    lifecycle: ActionLifecycle | None = None,
) -> AskOutcome:
    """Runs the conversational coordinator turn and maps its terminal onto AskOutcome."""
    del disconnected
    budget = turn_budget or UNBOUNDED_BUDGET
    raw_reporter = progress if isinstance(progress, CoordinatorProgress) else None
    reporter = TurnThoughts(raw_reporter) if raw_reporter is not None else None

    if model is None:
        model = coordinator_composition.build_coordinator_model(resources, progress=reporter)
    if reporter is not None:
        model = _ThinkingAloudModel(model, reporter)
    if tools is None:
        tools = coordinator_composition.build_coordinator_tools(
            coord_turn,
            principal=principal,
            budget=budget,
            resources=resources,
            progress=reporter,
            lifecycle_run_id=lifecycle_run_id,
            idempotency_key=body.idempotency_key,
        )

    ctx = coord_turn.turn.ctx
    # The prepared turn's stage report is the one the answer carries, as on
    # every other route; the coordinator model is the route it records.
    stage_models = coord_turn.turn.stage_models
    stage_models.record_used(resolve_production_route(ModelPurpose.coordinator))

    try:
        terminal = await run_coordinator_turn(
            coord_turn.context,
            model=model,
            tools=tools,
            budget=budget,
            policy=policy,
            lifecycle=lifecycle,
        )
    finally:
        await tools.aclose()
        if raw_reporter is not None:
            raw_reporter.finish_thought()

    corr_id = lifecycle_run_id or (ctx.run_id if ctx else None)
    if not corr_id:
        from app.telemetry.correlation import current_correlation_id

        corr_id = current_correlation_id()
    usage = _extract_terminal_usage(corr_id)

    match terminal:
        case FinishedDraft() as finished:
            if reporter is not None:
                reporter.emit("answering")
            text = "\n\n".join(b.text for b in finished.draft.blocks)
            # The evidence the answer was written over travels with it: the
            # business result gives the snapshot and the restore reference,
            # the passages give the sources; the query type names which.
            bq_res = committed_business_result(finished.outcomes)
            passages = retrieved_passages(finished.outcomes)
            wire = bq_res.business_query if bq_res is not None else None
            return Answered(
                answer_text=text,
                sources=passages,
                query_type=_answer_query_type(
                    finished.answer_mode,
                    has_business=bq_res is not None,
                    has_documents=bool(passages),
                ),
                citations=CitationsPayload(parsed=False),
                stage_models=stage_models,
                final_producer_purpose=ModelPurpose.coordinator,
                ctx=ctx,
                completion_status=bq_res.completion_status if bq_res is not None else None,
                sql_stop_reason=bq_res.sql_stop_reason if bq_res is not None else None,
                business_query=wire,
                bq=bq_res,
                presentation=(
                    wire.envelope.presentation
                    if wire is not None and wire.envelope is not None
                    else None
                ),
                turn_result=bq_res.turn_result if bq_res is not None else None,
                answer_mode=finished.answer_mode,
                usage=usage,
            )

        case ClarifyRequested(question=clarify_q):
            # The person answers in the next turn; the coordinator reads it
            # from history, so no continuation ticket is needed.
            return FixedMessage(
                message=clarify_q,
                query_type="semantic",
                stage_models=stage_models,
                ctx=ctx,
            )

        case BusinessQueryTerminal(result=bq_res):
            if bq_res.raise_capability_unavailable:
                return CapabilityUnavailable(
                    detail=CAPABILITY_UNAVAILABLE_MESSAGE,
                    answer=None,
                    bq=bq_res,
                    ctx=ctx,
                    turn_result=bq_res.turn_result,
                )
            if bq_res.disposition == "clarification_required":
                # Same shape the structured route returns: the v2 service reads
                # the wire outcome to mint the clarify ticket and choices.
                return Answered(
                    answer_text=bq_res.answer_text,
                    sources=(),
                    query_type="structured",
                    citations=CitationsPayload(parsed=False),
                    stage_models=stage_models,
                    final_producer_purpose=ModelPurpose.coordinator,
                    ctx=ctx,
                    completion_status=bq_res.completion_status,
                    sql_stop_reason=bq_res.sql_stop_reason,
                    business_query=bq_res.business_query,
                    bq=bq_res,
                    disambiguation=bq_res.disambiguation,
                    turn_result=bq_res.turn_result,
                    usage=usage,
                )
            return FixedMessage(
                message=bq_res.answer_text,
                query_type="structured",
                stage_models=stage_models,
                ctx=ctx,
            )

        case CoordinatorStopped(reason=reason):
            if reason == "cancelled":
                # The client went away; the service cancels the turn.
                return AskStopped(
                    query_type="structured",
                    stage_models=stage_models,
                    ctx=ctx,
                    usage=usage,
                )
            # Every other stop is the coordinator's own limit or a bad model
            # reply: the person reads what to do next, in the shared copy.
            return FixedMessage(
                message=_STOP_MESSAGES.get(reason) or copy_for_reason(None),
                query_type="semantic",
                stage_models=stage_models,
                ctx=ctx,
            )
