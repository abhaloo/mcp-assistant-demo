"""Shared pre-dispatch setup for JSON and SSE ask paths."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

from opentelemetry.trace import Span
from pydantic import ValidationError

from app.auth import Principal
from app.business_query.plan.dialogue import AssistantTurn, DialogueTurn, UserTurn
from app.business_query.plan.plan_diff import PlanDigest
from app.business_query.plan.query_plan import BusinessPeriod
from app.business_query.wire.module import BusinessQueryOwnerHint
from app.config import settings
from app.conversation.coordinator.contracts import (
    MAX_HISTORY_EXCHANGES,
    ClarificationSelection,
    CoordinatorContext,
    HistoryLine,
    PageRecord,
    ShownMember,
)
from app.conversation.followup_context import source_candidates, source_focus
from app.conversation.greetings import greeting_reply
from app.conversation.reference_artifact import (
    ReferenceArtifact,
    build_reference_artifact,
    references_from_page_records,
    references_from_record_context,
    references_from_record_rows,
)
from app.conversation.rehydration import (
    NoReferenceContext,
    ReferenceContextAvailable,
    ReferenceContextUnavailable,
    RehydrationResult,
)
from app.conversation.rehydration_service import (
    RECORDS_NO_LONGER_AVAILABLE_MESSAGE,
    REHYDRATION_UNAVAILABLE_MESSAGE,
)
from app.conversation.transcript_models import BqTurnDigest, TranscriptTurn
from app.conversation.turn import TurnContext, condenses_follow_ups, resolve_turn
from app.core.ask_errors import (
    DOCUMENT_UNAVAILABLE_MESSAGE,
    resolve_production_route,
    rethrow_model_route_denial,
)
from app.core.errors import (
    ContinuationClaimRejectedError,
    DocumentUnavailableError,
    NotFoundError,
)
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget, await_with_budget
from app.models.schemas import QueryType, Question
from app.models.tool_results import TurnResult
from app.providers.model_purpose import ModelPurpose
from app.providers.model_registry import PolicyViolationError
from app.providers.route_policy import RouteResolutionError
from app.providers.stage_model_report import StageModelAccumulator
from app.rag.access_tiers import document_tiers_for
from app.rag.page_context import compile_page_context_policy
from app.rag.query_classifier import (
    StructuredRouteContext,
    classify_query,
    route_context_for_principal,
)
from app.resources import ProcessResources
from app.services.access import resolve_access_tiers
from app.services.account_budget import (
    DENIED_TURN_RESULT,
    AccountBudgetGate,
    BudgetExhaustedError,
    BudgetUnconfiguredError,
    BudgetUnverifiableError,
    BudgetVerdict,
)
from app.services.ask_v2_reason_copy import copy_for_reason
from app.services.continuation_tokens import (
    claim_continuation_token,
    claim_pending_continuation,
    load_pending_continuation_payload,
    verify_clarify_ticket,
)
from app.services.owner_hint import owner_hint_for_context
from app.telemetry import classify_span
from app.telemetry.helpers import record_conversation_attributes

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BusinessQueryReplyContext:
    """A clarification reply's recovered turn facts, read from the ticket.

    ``question`` is the stalled turn's original question; ``continuation``
    the module marker that keeps a second stall a real timeout; ``reply``
    the person's narrowing text; ``prompt`` the clarification question the
    person was shown, so the re-plan sends the round-trip as dialogue.
    """

    question: str
    continuation: str | None
    reply: str | None
    prompt: str | None = None
    origin: Literal["coordinator", "structured"] = "structured"
    choice_id: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class PreparedTurn:
    """Shared turn facts every pre-dispatch variant carries."""

    ctx: TurnContext
    access_tiers: list[str]
    stage_models: StageModelAccumulator
    owner_hint: BusinessQueryOwnerHint | None = None
    bq_reply: BusinessQueryReplyContext | None = None
    # Structured prior turns for the planner — empty when conversation is off.
    bq_history: tuple[DialogueTurn, ...] = ()
    # Business answer each prior exchange can be patched against, by exchange id.
    continuation_subjects: Mapping[str, str] = field(default_factory=dict)
    budget: BudgetVerdict | None = None


def bq_plan_question(body: Question, turn: PreparedTurn) -> str:
    """Question the planner sees: the user's text, not the condenser rewrite.

    A clarification re-plan uses the stalled turn's question. Classify and RAG
    still use ``ctx.search_query``.
    """
    stalled = turn.bq_reply.question if turn.bq_reply is not None else None
    if stalled:
        return stalled
    if body.question:
        return body.question
    return turn.ctx.original_question


_MAX_BQ_HISTORY_EXCHANGES = 4


def _stored_period(raw: Mapping[str, object] | None) -> BusinessPeriod | None:
    """The period a stored digest carries, or None when it does not validate: one
    bad stored row must not fail every later turn of the thread."""
    if not raw:
        return None
    try:
        return BusinessPeriod.model_validate(raw)
    except ValidationError as exc:
        logger.warning("stored plan period dropped: %d validation errors", exc.error_count())
        return None


def plan_digest_from(digest: BqTurnDigest | None) -> PlanDigest | None:
    if digest is None or digest.outcome in ("timeout", "stopped"):
        return None
    return PlanDigest(
        grain=digest.grain,
        anchor=digest.anchor,
        dimensions=tuple(digest.dimensions),
        measures=tuple(digest.measures),
        limit=digest.limit or 20,
        period=_stored_period(digest.period),
        set_ids=tuple(digest.set_ids),
        plan_fingerprint=digest.plan_fingerprint,
    )


def assistant_turn_from_digest(digest: BqTurnDigest) -> AssistantTurn:
    if digest.outcome == "timeout":
        return AssistantTurn(text="no answer: the request timed out")
    if digest.outcome == "stopped":
        return AssistantTurn(text="no answer: stopped by the person")
    plan_digest = plan_digest_from(digest)
    return AssistantTurn(
        text=digest.receipt_title,
        answer_query_id=digest.answer_query_id,
        digest=plan_digest,
    )


def exchange_pairs(
    history: Sequence[TranscriptTurn],
) -> list[tuple[TranscriptTurn, TranscriptTurn | None]]:
    """Pair user and assistant turns by exchange_id in order of appearance."""
    by_eid: dict[str, dict[str, TranscriptTurn]] = {}
    order: list[str] = []
    for turn in history:
        if turn.exchange_id is None:
            continue
        if turn.exchange_id not in by_eid:
            by_eid[turn.exchange_id] = {}
            order.append(turn.exchange_id)
        by_eid[turn.exchange_id][turn.role] = turn
    return [(by_eid[e]["user"], by_eid[e].get("assistant")) for e in order if "user" in by_eid[e]]


def select_bq_history(
    history: Sequence[TranscriptTurn],
    *,
    conversation_enabled: bool,
) -> tuple[DialogueTurn, ...]:
    """Pick the last structured exchanges from already-loaded transcript turns."""
    if not conversation_enabled or not history:
        return ()
    qualifying = [
        (u, a) for u, a in exchange_pairs(history) if a is not None and a.bq_digest is not None
    ]
    selected: list[DialogueTurn] = []
    for user, assistant in qualifying[-_MAX_BQ_HISTORY_EXCHANGES:]:
        selected.append(UserTurn(text=user.content))
        if assistant.bq_digest is not None:
            selected.append(assistant_turn_from_digest(assistant.bq_digest))
        else:
            selected.append(AssistantTurn(text=assistant.content[:200]))
    return tuple(selected)


def coordinator_history(history: Sequence[TranscriptTurn]) -> tuple[HistoryLine, ...]:
    """Build history lines for the conversational coordinator from transcript turns."""
    lines: list[HistoryLine] = []
    for user, assistant in exchange_pairs(history):
        assistant_text = ""
        digest = None
        summary = None
        shown: tuple[ShownMember, ...] = ()
        facts: tuple[str, ...] = ()
        documents: tuple[str, ...] = ()
        if assistant is not None:
            assistant_text = assistant.content[:600]
            if assistant.bq_digest is not None:
                digest = plan_digest_from(assistant.bq_digest)
                summary = assistant.bq_digest.summary
                facts = tuple(assistant.bq_digest.facts)
                if assistant.bq_digest.shown:
                    shown = tuple(
                        ShownMember(member=k, values=tuple(v))
                        for k, v in assistant.bq_digest.shown.items()
                    )
            if assistant.components:
                doc_titles = [
                    str(getattr(c, "evidence_digest", None) or c.invocation_id)
                    for c in assistant.components
                    if getattr(c, "status", None) == "succeeded"
                    and getattr(c, "tool", None) == "document_search"
                ]
                documents = tuple(doc_titles[:5])
        page_scope: Literal["current"] | None = "current" if user.context_mode == "jobs" else None
        lines.append(
            HistoryLine(
                exchange_id=user.exchange_id or "",
                user_text=user.content,
                assistant_text=assistant_text,
                digest=digest,
                summary=summary,
                shown=shown,
                facts=facts,
                documents=documents,
                page_scope=page_scope,
            )
        )
    return tuple(lines[-MAX_HISTORY_EXCHANGES:])


def continuation_subjects(history: Sequence[TranscriptTurn]) -> dict[str, str]:
    """Map exchange id to the business answer id a patch subject may name."""
    subjects: dict[str, str] = {}
    for turn in history:
        if turn.bq_digest is not None and turn.exchange_id is not None:
            answer_id = turn.bq_digest.plan_answer_query_id or turn.bq_digest.answer_query_id
            if answer_id is not None:
                subjects[turn.exchange_id] = answer_id
    return subjects


def _prepared_turn(
    ctx: TurnContext,
    access_tiers: list[str],
    stage_models: StageModelAccumulator,
    *,
    owner_hint: BusinessQueryOwnerHint | None = None,
    bq_reply: BusinessQueryReplyContext | None = None,
) -> PreparedTurn:
    return PreparedTurn(
        ctx=ctx,
        access_tiers=access_tiers,
        stage_models=stage_models,
        owner_hint=owner_hint,
        bq_reply=bq_reply,
        bq_history=select_bq_history(
            ctx.history,
            conversation_enabled=settings.conversation_enabled,
        ),
        continuation_subjects=continuation_subjects(ctx.history),
    )


@dataclass(frozen=True)
class DispatchClassified:
    turn: PreparedTurn
    query_type: QueryType


@dataclass(frozen=True)
class RecordsOnly:
    turn: PreparedTurn


@dataclass(frozen=True)
class PreparedFixedMessage:
    turn: PreparedTurn
    message: str
    query_type: QueryType
    continuation_token: str | None = None
    omitted_capabilities: tuple[str, ...] = ()
    banner: str | None = None
    reason_code: str | None = None
    budget: BudgetVerdict | None = None
    turn_result: TurnResult | None = None


@dataclass(frozen=True)
class ReplayCompleted:
    turn: PreparedTurn
    answer: dict
    continuation_request_id: str


@dataclass(frozen=True)
class ContinuationClaimed:
    turn: PreparedTurn
    continuation_request_id: str
    omitted_capabilities: tuple[str, ...] = ("document_search",)
    banner: str | None = None


@dataclass(frozen=True)
class PreparedCoordinatorTurn:
    """The coordinator route's pre-dispatch shape. ``turn`` is the same
    PreparedTurn every other route hands the service, so the page owner hint,
    access tiers and stage report travel the same way on both routes."""

    turn: PreparedTurn
    context: CoordinatorContext


PreparedAsk = (
    DispatchClassified
    | RecordsOnly
    | PreparedFixedMessage
    | ReplayCompleted
    | ContinuationClaimed
    | PreparedCoordinatorTurn
)


async def _claim_clarification_reply(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    thread_id: str | None,
) -> BusinessQueryReplyContext:
    """Require a principal-bound pending claim before a v1 clarification reply."""
    token = body.continuation_token
    if not token:
        raise DocumentUnavailableError("clarification_reply requires continuation_token")
    verified = verify_clarify_ticket(token)
    if verified is None:
        raise NotFoundError("Invalid or expired continuation token")
    jti = verified["jti"]
    request_id = body.idempotency_key or body.run_id or "implicit"
    key_hash = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    exec_id = body.run_id or request_id
    claim = await claim_pending_continuation(
        store=resources.conversation_store,
        resources=resources,
        jti=jti,
        execution_id=exec_id,
        idempotency_key_hash=key_hash,
        principal=principal,
        thread_id=thread_id,
    )
    # The v2 reply handler claims first and dispatches through here under the
    # same execution id. Any other caller holding the key is a duplicate and
    # must not run the turn a second time.
    if claim.status == "in_progress":
        raise ContinuationClaimRejectedError("Continuation already in progress")
    if claim.status not in ("claimed", "reclaimed"):
        raise ContinuationClaimRejectedError("Continuation claim rejected")
    payload = await load_pending_continuation_payload(
        store=resources.conversation_store,
        resources=resources,
        jti=jti,
    )
    origin = payload.pending.get("origin", "structured")
    reply = (body.question or "").strip() or None
    choice_id = None
    label = None
    for c in payload.pending.get("choices", []):
        if not isinstance(c, dict):
            continue
        if reply and (c.get("label") == reply or c.get("rewrite") == reply or c.get("id") == reply):
            choice_id = c.get("id")
            label = c.get("label")
            break
    return BusinessQueryReplyContext(
        question=payload.question,
        continuation=payload.pending.get("continuation"),
        reply=reply,
        prompt=payload.pending.get("question"),
        origin=origin,
        choice_id=choice_id,
        label=label,
    )


def prompt_pipeline_for(query_type: QueryType) -> str:
    """Name the prompt whose text is actually sent for this route.

    ``both`` sends the document-RAG prompt for the document half; the
    Business Query planner owns its own prompt and is versioned separately.
    """
    return "sql_agent" if query_type == "structured" else "document_rag"


def _document_tiers_for(principal: Principal) -> list[str]:
    """Select document tiers for the principal."""
    if principal.document_tiers is not None:
        return document_tiers_for(principal)
    return resolve_access_tiers(principal.role, principal.permissions)


async def resolve_context(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    stage_accumulator: StageModelAccumulator | None = None,
) -> tuple[TurnContext, list[str]]:
    """Resolve access tiers, conversation turn, and trusted context without classification."""
    access_tiers = _document_tiers_for(principal)
    try:
        ctx = await resolve_turn(body, principal, resources=resources)
    except (RouteResolutionError, PolicyViolationError) as exc:
        rethrow_model_route_denial(exc)
    if stage_accumulator is not None and condenses_follow_ups(body.operation) and ctx.history:
        stage_accumulator.record_used(resolve_production_route(ModelPurpose.conversation))
    if body.record_context is None and body.page_context is not None:
        policy = compile_page_context_policy(body.page_context)
        ctx = replace(ctx, page_context=body.page_context, policy=policy)
    return ctx, access_tiers


def reference_artifact_for_turn(
    body: Question,
    ctx: TurnContext,
    *,
    answer_query_type: QueryType | None = None,
) -> ReferenceArtifact | None:
    """Build ReferenceArtifact from the live source that grounded this turn's answer."""
    if body.record_context is not None:
        assert ctx.record_context is not None
        refs = references_from_record_context(ctx.record_context)
        return build_reference_artifact("global_search", refs)
    page_answered_locally = ctx.records_only or (
        answer_query_type == "semantic"
        and ctx.page_context is not None
        and ctx.record_context is None
    )
    if page_answered_locally:
        assert ctx.page_context is not None
        refs = references_from_page_records(
            ctx.page_context.resource_type, [record.id for record in ctx.page_context.records]
        )
        return build_reference_artifact("page_context", refs)
    if isinstance(ctx.rehydration_outcome, ReferenceContextAvailable):
        refs = references_from_record_rows(ctx.rehydration_outcome.records)
        return build_reference_artifact("record_tool", refs)
    return None


async def classify(
    ctx: TurnContext,
    body: Question,
    *,
    route_context: StructuredRouteContext | None = None,
    on_classified: Callable[[Span, QueryType], None] | None = None,
    stage_accumulator: StageModelAccumulator | None = None,
) -> QueryType:
    """Classify search_query with router telemetry."""
    if stage_accumulator is not None:
        stage_accumulator.record_used(resolve_production_route(ModelPurpose.classify))
    with classify_span() as classify_span_obj:
        try:
            if route_context is None:
                query_type = await classify_query(ctx.search_query)
            else:
                try:
                    query_type = await classify_query(
                        ctx.search_query,
                        route_context=route_context,
                    )
                except TypeError as exc:
                    # A few downstream tests inject the historical one-arg
                    # classifier seam.  Keep that seam compatible while real
                    # production calls always receive the signed context.
                    if "route_context" not in str(exc):
                        raise
                    query_type = await classify_query(ctx.search_query)
        except (RouteResolutionError, PolicyViolationError) as exc:
            rethrow_model_route_denial(exc)
        classify_span_obj.set_attribute("query_type", query_type)
        if ctx.thread_id is not None:
            record_conversation_attributes(
                classify_span_obj,
                thread_id=ctx.thread_id,
                turns=len(ctx.history),
                condensed=ctx.search_query != body.question,
            )
        if on_classified is not None:
            on_classified(classify_span_obj, query_type)
    return query_type


def _fixed_response_for_stale_outcome(outcome: RehydrationResult) -> str | None:
    """The ONE place that maps a rehydration outcome onto its fixed,
    source-independent user-facing message (forbidden == missing, never a
    count/type/id oracle). Exhaustive over the three-state model -- every
    branch is named explicitly (no bare wildcard standing in for "and
    everything else means no message"), so an outcome shape this function
    doesn't recognize is a bug, never silently treated as "no context"."""
    match outcome:
        case NoReferenceContext() | ReferenceContextAvailable():
            return None
        case ReferenceContextUnavailable(reason="revoked_or_missing"):
            return RECORDS_NO_LONGER_AVAILABLE_MESSAGE
        case ReferenceContextUnavailable(reason="transient"):
            return REHYDRATION_UNAVAILABLE_MESSAGE
        case _:
            raise AssertionError(f"unexpected rehydration outcome: {outcome!r}")


def _coordinator_turn(turn: PreparedTurn) -> PreparedCoordinatorTurn:
    """The coordinator route's shape over a prepared turn; the turn already
    carries the page owner hint, any clarification reply and the budget verdict."""
    ctx = turn.ctx
    owner_hint = turn.owner_hint
    bq_reply = turn.bq_reply
    history_lines = coordinator_history(ctx.history)
    candidates = source_candidates(ctx.history)
    focus = source_focus(ctx.history, candidates)
    page_scope: Literal["current", "all"] = "current" if owner_hint is not None else "all"
    page_record = (
        PageRecord(resource=owner_hint.resource_type, record_id=str(owner_hint.record_id))
        if owner_hint is not None
        else None
    )
    if bq_reply is not None:
        question = bq_reply.question
        if bq_reply.choice_id is not None or bq_reply.label is not None:
            selection = ClarificationSelection(
                prompt=bq_reply.prompt or "",
                choice_id=bq_reply.choice_id,
                label=bq_reply.label or bq_reply.reply,
            )
        else:
            selection = ClarificationSelection(
                prompt=bq_reply.prompt or "",
                free_text=bq_reply.reply or "",
            )
    else:
        question = ctx.original_question
        selection = None

    coord_ctx = CoordinatorContext(
        turn_id=ctx.run_id or "turn-1",
        question=question,
        history=history_lines,
        candidates=candidates,
        focus=focus,
        page_scope=page_scope,
        page_record=page_record,
        selection=selection,
    )
    return PreparedCoordinatorTurn(turn=turn, context=coord_ctx)


def with_budget(prepared: PreparedTurn, budget: BudgetVerdict | None) -> PreparedTurn:
    """Return the prepared turn carrying the admission verdict."""
    if budget is None:
        return prepared
    return replace(prepared, budget=budget)


def _budget_refusal(
    body: Question, code: str, budget: BudgetVerdict | None
) -> PreparedFixedMessage:
    q = body.question or ""
    ctx = TurnContext(body.thread_id, [], q, q, run_id=body.run_id or "")
    turn = PreparedTurn(ctx, [], StageModelAccumulator(), budget=budget)
    return PreparedFixedMessage(
        turn=turn,
        message=copy_for_reason(code),
        query_type="semantic",
        reason_code=code,
        budget=budget,
        turn_result=DENIED_TURN_RESULT,
    )


async def prepare_ask(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
    on_classified: Callable[[QueryType], None] | None = None,
) -> PreparedAsk:
    """The single pre-dispatch decision shared by JSON and SSE.

    Classifies the turn and resolves context *without* running the query.
    Only document-search-on classify asks a model in this phase.
    """
    budget_verdict: BudgetVerdict | None = None
    if body.operation != "result_page":
        try:
            budget_verdict = await AccountBudgetGate(resources).admit(
                principal, now=datetime.now(tz=UTC)
            )
        except BudgetExhaustedError as exc:
            return _budget_refusal(body, "budget_exhausted", exc.verdict)
        except BudgetUnconfiguredError:
            return _budget_refusal(body, "budget_unconfigured", None)
        except BudgetUnverifiableError:
            return _budget_refusal(body, "budget_unverifiable", None)

    stage_models = StageModelAccumulator()
    ctx, access_tiers = await await_with_budget(
        lambda: resolve_context(
            body, principal, resources=resources, stage_accumulator=stage_models
        ),
        turn_budget,
    )
    owner_hint = owner_hint_for_context(ctx)
    reference_resources = (
        tuple(sorted({record.resource_type for record in ctx.record_context.records}))
        if ctx.record_context is not None
        else ()
    )
    route_context = route_context_for_principal(
        principal,
        reference_resources=reference_resources,
        owner_resource=owner_hint.resource_type if owner_hint is not None else None,
    )

    def _turn(hint=owner_hint, reply=None) -> PreparedTurn:
        return with_budget(
            _prepared_turn(ctx, access_tiers, stage_models, owner_hint=hint, bq_reply=reply),
            budget_verdict,
        )

    if body.operation == "clarification_reply":
        bq_reply = await _claim_clarification_reply(
            body,
            principal,
            resources=resources,
            thread_id=ctx.thread_id,
        )
        if bq_reply.origin == "coordinator" and settings.conversation_coordinator_enabled:
            return _coordinator_turn(_turn(reply=bq_reply))
        return DispatchClassified(turn=_turn(reply=bq_reply), query_type="structured")

    if body.continuation_token:
        claim = await claim_continuation_token(
            body.continuation_token,
            principal=principal,
            thread_id=ctx.thread_id,
            question=body.question,
            request_id=body.idempotency_key or body.run_id,
        )
        if claim is None or claim.status in {"rejected", "in_progress"}:
            raise DocumentUnavailableError("Invalid or expired continuation token.")
        turn = _turn()
        request_id = body.idempotency_key or body.run_id or "implicit"
        if claim.status == "completed" and claim.answer is not None:
            return ReplayCompleted(
                turn=turn,
                answer=claim.answer,
                continuation_request_id=request_id,
            )
        return ContinuationClaimed(
            turn=turn,
            continuation_request_id=request_id,
            omitted_capabilities=("document_search",),
            banner=DOCUMENT_UNAVAILABLE_MESSAGE,
        )

    greeting = greeting_reply(body.question or ctx.original_question or "")
    if greeting is not None:
        return PreparedFixedMessage(
            turn=_turn(), message=greeting, query_type="semantic", budget=budget_verdict
        )

    if ctx.records_only and ctx.record_context is None:
        return RecordsOnly(turn=_turn())

    if ctx.record_context is None:
        fixed_response = _fixed_response_for_stale_outcome(ctx.rehydration_outcome)
        if fixed_response is not None:
            return PreparedFixedMessage(
                turn=_turn(), message=fixed_response, query_type="semantic", budget=budget_verdict
            )

    if settings.conversation_coordinator_enabled:
        return _coordinator_turn(_turn())

    query_type: QueryType
    if (
        ctx.record_context is not None
        and owner_hint is not None
        and principal.manifest_hash is not None
    ):
        query_type = "structured"
    elif ctx.record_context is not None:
        query_type = "semantic"
    else:
        if not settings.document_rag_enabled:
            query_type = "structured"
        else:
            query_type = await await_with_budget(
                lambda: classify(
                    ctx,
                    body,
                    route_context=route_context,
                    on_classified=on_classified,
                    stage_accumulator=stage_models,
                ),
                turn_budget,
            )

    return DispatchClassified(turn=_turn(), query_type=query_type)
