"""Shared pre-dispatch setup for JSON and SSE ask paths."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from opentelemetry.trace import Span

from app.auth import Principal
from app.business_query.wire.module import BusinessQueryOwnerHint
from app.config import settings
from app.conversation.coordinator.contracts import CoordinatorContext, HistoryLine
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
from app.conversation.turn import TurnContext, resolve_turn
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
from app.core.turn_budget import TurnBudget, await_with_budget
from app.models.schemas import QueryType, Question
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
from app.services.continuation_tokens import (
    claim_continuation_token,
    claim_pending_continuation,
    load_pending_continuation_payload,
    verify_clarify_ticket,
)
from app.services.owner_hint import owner_hint_for_context
from app.telemetry import classify_span
from app.telemetry.helpers import record_conversation_attributes


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


@dataclass(frozen=True)
class PreparedTurn:
    """Shared turn facts every pre-dispatch variant carries."""

    ctx: TurnContext
    access_tiers: list[str]
    stage_models: StageModelAccumulator
    owner_hint: BusinessQueryOwnerHint | None = None
    bq_reply: BusinessQueryReplyContext | None = None
    # Structured prior turns for the planner — empty when conversation is off.
    bq_history: tuple[tuple[str, str], ...] = ()


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
_MAX_BQ_HISTORY_DIGEST_CHARS = 200


def _ai_text_from_bq_digest(digest: BqTurnDigest) -> str:
    """Render planner AI-turn text from a structured digest (never raw answer)."""
    if digest.outcome == "timeout":
        return "no answer: the request timed out"
    title = digest.receipt_title
    if digest.scalar_value is not None:
        text = f"answered: {title} = {digest.scalar_value}"
    else:
        text = f"answered: {title}, {digest.row_count} rows"
    return text[:_MAX_BQ_HISTORY_DIGEST_CHARS]


def select_bq_history(
    history: Sequence[TranscriptTurn],
    *,
    conversation_enabled: bool,
) -> tuple[tuple[str, str], ...]:
    """Pick the last structured exchanges from already-loaded transcript turns.

    Keeps only assistant turns with ``bq_digest``. Pairs by ``exchange_id``.
    AI-turn text comes from the digest render form. Empty when conversation is
    disabled or history has no qualifying pairs. Does not load the conversation
    store.
    """
    if not conversation_enabled or not history:
        return ()

    by_eid: dict[str, dict[str, TranscriptTurn]] = {}
    order: list[str] = []
    for turn in history:
        eid = turn.exchange_id
        if eid is None:
            continue
        if eid not in by_eid:
            by_eid[eid] = {}
            order.append(eid)
        by_eid[eid][turn.role] = turn

    qualifying: list[tuple[TranscriptTurn, TranscriptTurn]] = []
    for eid in order:
        pair = by_eid[eid]
        user = pair.get("user")
        assistant = pair.get("assistant")
        if user is None or assistant is None or assistant.bq_digest is None:
            continue
        qualifying.append((user, assistant))

    selected: list[tuple[str, str]] = []
    for user, assistant in qualifying[-_MAX_BQ_HISTORY_EXCHANGES:]:
        assert assistant.bq_digest is not None
        selected.append(("human", user.content))
        selected.append(("ai", _ai_text_from_bq_digest(assistant.bq_digest)))
    return tuple(selected)


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
    return BusinessQueryReplyContext(
        question=payload.question,
        continuation=payload.pending.get("continuation"),
        reply=(body.question or "").strip() or None,
        prompt=payload.pending.get("question"),
    )


def prompt_pipeline_for(query_type: QueryType) -> str:
    """Name the prompt whose text is actually sent for this route.

    ``both`` sends the document-RAG prompt for the document half; the
    Business Query planner owns its own prompt and is versioned separately.
    """
    return "sql_agent" if query_type == "structured" else "document_rag"


def _document_tiers_for(principal: Principal) -> list[str]:
    """Document-tier source selection.

    A valid v2 principal (a strictly validated ``record_access`` claim — see
    ``app/auth/jwt.py``) carries its own explicit, signed ``document_tiers``;
    those are the source of truth and ``get_access_tiers`` is never
    consulted. v1 principals (``document_tiers is None``) keep the existing
    role/permission derivation completely untouched — Billing does not emit
    v2 claims yet, so this branch is unreachable in production today.
    """
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
    """Resolve access tiers, conversation turn, and trusted context — no classify.

    Precedence: ``resolve_turn()`` is authoritative for ``ctx.record_context``
    — it already applies the full precedence order (live record_context,
    then live page_context, then a rehydrated ledger, then none), so this
    function never re-derives or overwrites it. ``body.page_context`` is
    compiled onto its own TurnContext field ONLY when there is no live
    ``body.record_context`` — never reintroducing a record/page dual context.
    A page policy may be a records-only dataset or an ambient owner hint;
    only the former bypasses classification.  Registered page profiles are
    ambient by default; records-only remains an explicit compatibility mode
    for callers that construct a legacy policy directly.
    """
    access_tiers = _document_tiers_for(principal)
    try:
        ctx = await resolve_turn(body, principal, resources=resources)
    except (RouteResolutionError, PolicyViolationError) as exc:
        rethrow_model_route_denial(exc)
    if stage_accumulator is not None and ctx.history:
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
    """Builds the ``ReferenceArtifact`` from exactly the ONE live source that
    grounded THIS turn's answer -- mirrors the same precedence
    ``resolve_turn()`` applies to ``ctx.record_context`` itself (a live
    ``body.record_context`` wins over page context, which wins over a
    rehydrated ledger), rather than re-deriving a competing precedence
    here. Both ``persist_and_enrich`` call sites (JSON ``_finalize_answer``
    and SSE ``_commit_done``) call this once per turn; an empty reference
    set already yields ``None`` (``build_reference_artifact``'s existing
    pin), so no extra empty-check is needed here.

    - ``global_search``: built from ``ctx.record_context`` only when THIS
      request's body actually carried one -- checking ``body.record_context``
      rather than ``ctx.record_context`` distinguishes that from a
      rehydrated record_context (which also lands on ``ctx.record_context``
      for the answer prompt, but is NOT this turn's own global_search
      input).
    - ``page_context``: built from the trusted page records Laravel sent this
      request when the page-record path actually produced the answer, or when
      an ambient page supplied a semantic answer. The latter is needed for a
      legacy Jobs index: its rows are optional ambient context, while a
      structured question must remain global and must not persist the visible
      page as if it were the Business Query result. ``answer_query_type`` is
      therefore a deliberate input to this provenance decision. The alias
      (work_order -> job) applies inside ``build_reference_artifact``.
    - ``record_tool``: built from a ``ReferenceContextAvailable`` outcome's
      own ``RecordRow`` list -- these rows came from the policy-scoped
      record engine (see ``app/policy/record_executor.py``), the exact same
      shape/source ``references_from_record_rows`` was built for;
      re-persisting them each turn keeps the ledger fresh (refreshes TTL /
      becomes the new nearest artifact) without needing a distinct source
      label. Checked last -- below both live inputs.
    - No producer fires (plain document/SQL turn, or nothing survived
      rehydration): ``None``.
    """
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
        case NoReferenceContext():
            return None
        case ReferenceContextAvailable():
            return None
        case ReferenceContextUnavailable(reason="revoked_or_missing"):
            return RECORDS_NO_LONGER_AVAILABLE_MESSAGE
        case ReferenceContextUnavailable(reason="transient"):
            return REHYDRATION_UNAVAILABLE_MESSAGE
        case _:
            raise AssertionError(f"unexpected rehydration outcome: {outcome!r}")


async def prepare_ask(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    on_classified: Callable[[Span, QueryType], None] | None = None,
    turn_budget: TurnBudget,
) -> PreparedAsk:
    """The one place JSON and SSE decide records-only / a fixed pre-dispatch
    message / classification -- both transports share this instead of
    computing it independently. Context precedence itself stays in
    ``resolve_turn()`` -- this function only CONSUMES ``ctx.record_context``,
    it never re-derives which source won.

    Decision order (mirrors ``resolve_turn()``'s own explicit-context
    precedence):

    1. records-only (a page context whose compiled policy explicitly says so)
       -- but only when there is no live ``ctx.record_context`` (an
       explicit record_context always outranks ambient records_only, even
       for a forged request somehow carrying both -- ``resolve_context()``
       already keeps them mutually exclusive in the real flow; the extra
       check here is defense in depth). Classification never runs.
    2. no live ``ctx.record_context`` + a ``ReferenceContextUnavailable``
       outcome from an earlier turn -- a fixed, source-independent message
       keyed off ``reason`` (``revoked_or_missing`` / ``transient``), never
       a guess. A live ``record_context`` from THIS turn always outranks a
       stale outcome -- rehydration itself never populates
       ``ctx.record_context`` for this outcome, so reaching this branch
       already proves nothing live superseded it.
    3. a single live ``ctx.record_context`` under a v2 authorization snapshot
       -- the trusted record becomes the Business Query owner and routes to
       ``structured`` without a wording classifier. Multi-record and legacy
       v1 contexts retain the semantic compatibility path.
    4. otherwise, if document search is off, dispatch ``structured`` without
       classify; if document search is on, classify.

    Only document-search-on classify asks a model in this phase.
    """
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

    if getattr(body, "operation", None) == "clarification_reply":
        bq_reply = await _claim_clarification_reply(
            body,
            principal,
            resources=resources,
            thread_id=ctx.thread_id,
        )
        return DispatchClassified(
            turn=_prepared_turn(
                ctx,
                access_tiers,
                stage_models,
                owner_hint=owner_hint,
                bq_reply=bq_reply,
            ),
            query_type="structured",
        )

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
        turn = _prepared_turn(ctx, access_tiers, stage_models, owner_hint=owner_hint)
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
            turn=_prepared_turn(ctx, access_tiers, stage_models, owner_hint=owner_hint),
            message=greeting,
            query_type="semantic",
        )

    records_only = ctx.records_only and ctx.record_context is None
    if records_only:
        return RecordsOnly(
            turn=_prepared_turn(ctx, access_tiers, stage_models, owner_hint=owner_hint)
        )

    if ctx.record_context is None:
        fixed_response = _fixed_response_for_stale_outcome(ctx.rehydration_outcome)
        if fixed_response is not None:
            return PreparedFixedMessage(
                turn=_prepared_turn(ctx, access_tiers, stage_models, owner_hint=owner_hint),
                message=fixed_response,
                query_type="semantic",
            )

    if settings.conversation_coordinator_enabled:
        history_lines = tuple(
            HistoryLine(exchange_id=t.exchange_id or "", user_text=t.content)
            for t in ctx.history
            if t.role == "user"
        )[-8:]
        candidates = source_candidates(ctx.history)
        focus = source_focus(ctx.history, candidates)
        coord_ctx = CoordinatorContext(
            turn_id=ctx.run_id or body.run_id or "turn-1",
            question=body.question or ctx.original_question,
            history=history_lines,
            candidates=candidates,
            focus=focus,
        )
        return PreparedCoordinatorTurn(
            turn=_prepared_turn(ctx, access_tiers, stage_models, owner_hint=owner_hint),
            context=coord_ctx,
        )

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

    return DispatchClassified(
        turn=_prepared_turn(ctx, access_tiers, stage_models, owner_hint=owner_hint),
        query_type=query_type,
    )
