"""Application service adapter connecting CoordinatorTerminal to AskOutcome."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from app.auth import Principal
from app.business_query.definitions import (
    DefinitionBundle,
    bundle_for_manifest,
)
from app.business_query.definitions.schema import UiDestination
from app.business_query.outcomes import BusinessQueryWireOutcome
from app.business_query.wire.module_scoping import load_bundle
from app.conversation.coordinator.action_lifecycle import ActionLifecycle
from app.conversation.coordinator.contracts import (
    COORDINATOR_CLARIFY,
    ActionKind,
    Observation,
)
from app.conversation.coordinator.policy import CoordinatorPolicy, scaled_coordinator_policy
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
from app.models.ask_v2_events import FollowUpAction
from app.models.citations import CitationsPayload
from app.models.schemas import QueryType, Question
from app.models.ui_link import UiLink
from app.providers.model_purpose import ModelPurpose
from app.query_records.context import TerminalUsageCapture
from app.rag.provenance.ui_links import permitted_destinations, resolve_page_slots
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
from app.services.ask_prepare import PreparedCoordinatorTurn
from app.services.ask_v2_reason_copy import copy_for_reason
from app.services.business_query_service import AskBusinessQueryResult
from app.services.coordinator_answer import (
    cited_passages,
    committed_business_result,
    own_turn_result,
    restored_exchange_ids,
    restored_refs,
    retrieved_passages,
    stop_turn_result,
)
from app.services.coordinator_thoughts import (
    TurnThoughts,
    member_words,
    person_note,
    plain_words,
)
from app.services.coordinator_tools import CoordinatorProgress
from app.services.follow_up_offer import accept_offer_for_principal, unreachable_record_types
from app.telemetry.invocation_ledger import aggregate_turn_usage

if TYPE_CHECKING:
    from app.conversation.coordinator.contracts import (
        CoordinatorAction,
        CoordinatorContext,
    )
    from app.conversation.coordinator.model import CoordinatorModel
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

# FollowUpAction.label and .prompt need at least two characters (ask_v2_events.py);
# AnswerBlock.text allows one, so a shorter suggestion is skipped.
_MIN_OFFER_CHARS = 2
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _AnswerWording:
    """What the answer's copy may say: the pages it may link, their labels and
    the viewer's plain words for tool and member names."""

    offered: tuple[UiDestination, ...]
    labels: dict[str, str]
    words: Mapping[str, str] | None
    correlation_id: str


def _log_page_links(correlation_id: str, counts: tuple[int, int, int, int, int]) -> None:
    offered, linked, label_only, removed, guard_stripped = counts
    logger.info(
        "page_links correlation_id=%s offered=%s linked=%s label_only=%s removed=%s"
        " guard_stripped=%s",
        correlation_id,
        offered,
        linked,
        label_only,
        removed,
        guard_stripped,
    )


def _page_slot_context(
    bundle: object, principal: Principal, observations: tuple[Observation, ...]
) -> tuple[tuple[UiDestination, ...], dict[str, str]]:
    if not isinstance(bundle, DefinitionBundle):
        return (), {}
    offered_keys = {key for obs in observations for key in obs.page_keys}
    offered = tuple(d for d in permitted_destinations(bundle, principal) if d.key in offered_keys)
    labels = {d.key: d.label for d in bundle.ui_destinations}
    return offered, labels


class _ThinkingAloudModel:
    """Narrates each decision to the progress sink as one short line."""

    def __init__(
        self,
        inner: CoordinatorModel,
        progress: CoordinatorProgress,
        words: Mapping[str, str] | None = None,
    ) -> None:
        self._inner = inner
        self._progress = progress
        self._words = words
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
        note = getattr(action, "note_to_person", None)
        line = person_note(note, self._words) or _THOUGHT_BY_ACTION.get(action.kind)
        if line is not None:
            self._progress.emit_thought_delta(line)
        return action


def _answered_over(bq: AskBusinessQueryResult | None) -> AskBusinessQueryResult | None:
    """A business result an answer was written over. The answer is the turn's
    reply, so a denial under it no longer raises the capability error."""
    if bq is None or not bq.raise_capability_unavailable:
        return bq
    return replace(bq, raise_capability_unavailable=False)


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


def _follow_up_slot_copy(
    finished: FinishedDraft, offered: tuple[UiDestination, ...], labels: dict[str, str]
) -> tuple[list[str], str | None, int, int, int]:
    label_only = removed = guard_stripped = 0
    suggestions: list[str] = []
    for block in finished.draft.blocks:
        if block.claim_type != "suggestion":
            continue
        res = resolve_page_slots(block.text.strip(), offered, labels)
        label_only += res.label_only + res.linked
        removed += res.removed
        guard_stripped += res.guard_stripped
        if len(res.text) >= _MIN_OFFER_CHARS:
            suggestions.append(res.text)
    part = finished.draft.unanswered_part
    if part is not None:
        res = resolve_page_slots(part, offered, labels)
        label_only += res.label_only + res.linked
        removed += res.removed
        guard_stripped += res.guard_stripped
        part = res.text
    return suggestions, part, label_only, removed, guard_stripped


def _prose_slot_copy(
    source_blocks: Sequence[object],
    offered: tuple[UiDestination, ...],
    labels: dict[str, str],
) -> tuple[list[str], list[UiLink], int, int, int]:
    label_only = removed = guard_stripped = 0
    resolved_parts: list[str] = []
    collected: list[UiLink] = []
    seen_keys: set[str] = set()
    for block in source_blocks:
        res = resolve_page_slots(block.text, offered, labels)
        if block.claim_type != "suggestion":
            label_only += res.label_only
            removed += res.removed
            guard_stripped += res.guard_stripped
        resolved_parts.append(res.text)
        if block.claim_type == "suggestion":
            continue
        for link in res.links:
            if link.key in seen_keys:
                continue
            seen_keys.add(link.key)
            collected.append(link)
    return resolved_parts, collected, label_only, removed, guard_stripped


def _answered_from_finished_draft(
    finished: FinishedDraft,
    principal: Principal,
    coord_turn: PreparedCoordinatorTurn,
    usage: TerminalUsageCapture | None,
    wording: _AnswerWording,
) -> Answered:
    """Resolve page slots and plain words, then map the finished draft to an answered turn."""
    ctx = coord_turn.turn.ctx
    stage_models = coord_turn.turn.stage_models
    question = coord_turn.context.question
    offered, labels, words = wording.offered, wording.labels, wording.words
    prose = [b for b in finished.draft.blocks if b.claim_type != "suggestion"]
    suggestions, part, label_only, removed, guard_stripped = _follow_up_slot_copy(
        finished, offered, labels
    )
    offer_prompts = ([part] if part else []) + [plain_words(s, words) for s in suggestions]
    source_blocks = prose or list(finished.draft.blocks)
    resolved_parts, collected, lo, rm, gs = _prose_slot_copy(source_blocks, offered, labels)
    _log_page_links(
        wording.correlation_id,
        (len(offered), len(collected), label_only + lo, removed + rm, guard_stripped + gs),
    )
    text = plain_words("\n\n".join(resolved_parts), words)
    offer = (
        accept_offer_for_principal(
            [
                FollowUpAction(id=f"fu-c{i}", label=s[:80], prompt=s[:160])
                for i, s in enumerate(offer_prompts, start=1)
            ],
            question=question,
            principal=principal,
        )
        if prose and offer_prompts
        else None
    )
    # The evidence the answer was written over travels with it: the business
    # result gives the snapshot and the restore reference, the cited passages
    # give the sources; the query type names which.
    bq_res = _answered_over(committed_business_result(finished.outcomes))
    passages = retrieved_passages(finished.outcomes)
    cited = cited_passages(finished.draft, passages)
    wire = bq_res.business_query if bq_res is not None else None
    return Answered(
        answer_text=text,
        sources=cited.sources,
        query_type=_answer_query_type(
            finished.answer_mode,
            has_business=bq_res is not None,
            has_documents=bool(passages),
        ),
        citations=cited.citations,
        stage_models=stage_models,
        final_producer_purpose=ModelPurpose.coordinator,
        text_kind=finished.text_kind,
        ctx=ctx,
        completion_status=bq_res.completion_status if bq_res is not None else None,
        sql_stop_reason=bq_res.sql_stop_reason if bq_res is not None else None,
        business_query=wire,
        bq=bq_res,
        presentation=(
            wire.envelope.presentation if wire is not None and wire.envelope is not None else None
        ),
        turn_result=(
            bq_res.turn_result if bq_res is not None else own_turn_result(finished.outcomes)
        ),
        answer_mode=finished.answer_mode,
        source_exchange_ids=restored_exchange_ids(finished.outcomes),
        source_restore_refs=restored_refs(finished.outcomes),
        usage=usage,
        follow_up_offer=offer,
        unanswered_part=part,
        ui_links=tuple(collected),
    )


def _turn_correlation_id(
    lifecycle_run_id: str | None, body: Question, coord_turn: PreparedCoordinatorTurn
) -> str:
    corr_id = (
        lifecycle_run_id
        or body.run_id
        or (coord_turn.turn.ctx.run_id if coord_turn.turn.ctx else "")
    )
    if corr_id:
        return corr_id
    from app.telemetry.correlation import current_correlation_id

    return current_correlation_id()


def _bundle_slot_labels(principal: Principal, correlation_id: str) -> tuple[object, dict[str, str]]:
    bundle = load_bundle(bundle_for_manifest, principal, correlation_id)
    labels = (
        {d.key: d.label for d in bundle.ui_destinations}
        if isinstance(bundle, DefinitionBundle)
        else {}
    )
    return bundle, labels


def _viewer_words(principal: Principal, bundle: object) -> dict[str, str] | None:
    """The viewer's card member words; None when no accepted bundle fits the viewer."""
    return member_words(principal, bundle) if isinstance(bundle, DefinitionBundle) else None


def _thoughts_reporter(
    progress: object, words: Mapping[str, str] | None, labels: dict[str, str]
) -> TurnThoughts | None:
    if not isinstance(progress, CoordinatorProgress):
        return None
    return TurnThoughts(progress, words=words, slot_labels=labels)


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
    policy: CoordinatorPolicy | None = None,
    lifecycle: ActionLifecycle | None = None,
) -> AskOutcome:
    """Runs the conversational coordinator turn and maps its terminal onto AskOutcome."""
    del disconnected
    budget = turn_budget or UNBOUNDED_BUDGET
    corr_id = _turn_correlation_id(lifecycle_run_id, body, coord_turn)
    bundle, labels = _bundle_slot_labels(principal, corr_id)
    # A streamed turn words its thoughts and its answer in the viewer's words.
    streamed = isinstance(progress, CoordinatorProgress)
    words = _viewer_words(principal, bundle) if streamed else None
    reporter = _thoughts_reporter(progress, words, labels)

    if policy is None:
        policy = scaled_coordinator_policy()

    if model is None:
        model = coordinator_composition.build_coordinator_model(resources)
    if reporter is not None:
        model = _ThinkingAloudModel(model, reporter, words)
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
            coord_turn.context.model_copy(
                update={"unreachable": unreachable_record_types(principal)}
            ),
            model=model,
            tools=tools,
            budget=budget,
            policy=policy,
            lifecycle=lifecycle,
        )
    finally:
        await tools.aclose()
        if reporter is not None:
            reporter.finish_thought()

    usage = await aggregate_turn_usage(corr_id)

    match terminal:
        case FinishedDraft() as finished:
            if reporter is not None:
                reporter.emit("answering")
            offered, _ = _page_slot_context(bundle, principal, finished.observations)
            return _answered_from_finished_draft(
                finished,
                principal,
                coord_turn,
                usage,
                _AnswerWording(offered=offered, labels=labels, words=words, correlation_id=corr_id),
            )

        case ClarifyRequested(
            question=clarify_q, choices=clarify_choices, outcomes=clarify_outcomes
        ):
            res = resolve_page_slots(clarify_q, (), labels)
            _log_page_links(
                corr_id,
                (0, 0, res.label_only + res.linked, res.removed, res.guard_stripped),
            )
            if not clarify_choices:
                # The model offered no choices even after a repair; the person
                # reads the question and answers it in the next turn.
                outcome: AskOutcome = FixedMessage(
                    message=res.text,
                    query_type="semantic",
                    stage_models=stage_models,
                    ctx=ctx,
                    usage=usage,
                    bq=committed_business_result(clarify_outcomes),
                )
            else:
                # The person answers on the card; the reply path sends the chosen
                # label or their own words back as the next question.
                outcome = Answered(
                    answer_text=res.text,
                    sources=(),
                    query_type="structured",
                    citations=CitationsPayload(parsed=False),
                    stage_models=stage_models,
                    final_producer_purpose=ModelPurpose.coordinator,
                    ctx=ctx,
                    business_query=BusinessQueryWireOutcome(
                        outcome="clarification_required",
                        question=res.text,
                        continuation=COORDINATOR_CLARIFY,
                        choices=[
                            {"id": f"opt-{n}", "label": resolve_page_slots(label, (), labels).text}
                            for n, label in enumerate(clarify_choices, start=1)
                        ],
                    ),
                    bq=committed_business_result(clarify_outcomes),
                    usage=usage,
                )
            return outcome

        case BusinessQueryTerminal(result=bq_res):
            if bq_res.raise_capability_unavailable:
                return CapabilityUnavailable(
                    detail=CAPABILITY_UNAVAILABLE_MESSAGE,
                    answer=None,
                    bq=bq_res,
                    ctx=ctx,
                    turn_result=bq_res.turn_result,
                    usage=usage,
                )
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

        case CoordinatorStopped(reason=reason, outcomes=stop_outcomes):
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
                reason_code=reason,
                usage=usage,
                bq=committed_business_result(stop_outcomes),
                turn_result=stop_turn_result(stop_outcomes),
            )
