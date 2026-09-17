"""Conversation turn resolution and transcript persistence for JSON + streaming."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.auth import Principal
from app.config import settings
from app.conversation.condense import condense_question
from app.conversation.reference_artifact import ReferenceArtifact
from app.conversation.rehydration import NoReferenceContext, RehydrationResult
from app.conversation.rehydration_service import (
    record_context_from_rehydration,
    rehydrate_for_followup,
)
from app.conversation.thread_loading import (
    LoadedThread,
    _history_without_exchange,
    _latest_exchange_id,
    _load_history,
    _resolve_thread_id,
    load_thread,
)
from app.conversation.transcript import build_exchange_turns, to_messages
from app.conversation.transcript_models import BqTurnDigest, ContextMode, TranscriptTurn
from app.conversation.transcript_store import (
    ConversationStore,
    ThreadOwnershipError,
    get_conversation_store,
    new_exchange_id,
    new_thread_id,
)
from app.core.ask_errors import rethrow_model_route_denial
from app.core.errors import (
    RegenerateConflictError,
    ServiceUnavailableError,
)

__all__ = [
    "ConversationStore",
    "LoadedThread",
    "PersistedExchange",
    "ThreadOwnershipError",
    "TurnContext",
    "_history_without_exchange",
    "_latest_exchange_id",
    "_load_history",
    "_resolve_thread_id",
    "finalize_exchange",
    "get_conversation_store",
    "load_thread",
    "new_exchange_id",
    "new_thread_id",
    "persist_exchange",
    "resolve_turn",
]
from app.models.page_context_v2 import PageContextV2
from app.models.record_context import RecordContext
from app.models.schemas import Question, TrustedPageContext
from app.models.tool_results import ToolResultFields
from app.providers.model_registry import PolicyViolationError
from app.providers.route_policy import RouteResolutionError
from app.rag.chains.document_chain import RagTurnInput
from app.rag.page_context import PageContextPolicy
from app.resources import ProcessResources
from app.services.record_intent import RecordAggregateContinuation
from app.telemetry import run_in_thread
from app.telemetry.helpers import conversation_thread_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PersistedExchange:
    thread_id: str | None
    exchange_id: str | None


@dataclass(frozen=True)
class TurnContext:
    thread_id: str | None
    history: list[TranscriptTurn]
    search_query: str
    original_question: str
    page_context: TrustedPageContext | PageContextV2 | None = None
    policy: PageContextPolicy | None = None
    # Trusted record-context — Billing Global Search results, or a
    # rehydrated reference ledger. Distinct from page_context (the Jobs
    # trusted-page contract) above. Precedence (see resolve_turn() below):
    # a live body.record_context always wins; a live body.page_context (no
    # record_context) leaves this field None so the page policy can provide
    # either a records-only dataset or an ambient owner hint; only when
    # NEITHER live input is present can a rehydrated ledger populate this
    # field.
    record_context: RecordContext | None = None
    operation: str = "ask"
    target_exchange_id: str | None = None
    run_id: str | None = None
    condensed: bool = False
    # Live reference-ledger rehydration outcome. NOT optional: "not
    # attempted" (feature-off gate closed, empty thread, or no ledger in
    # history at all) is not a distinct `None` -- it is the SAME
    # NoReferenceContext() this field defaults to, so a consumer's
    # exhaustive match over RehydrationResult's three constructors never
    # needs a `None` arm. See app/conversation/rehydration.py for the
    # outcome contract; the ask-flow call sites
    # (app/services/ask_service.py, app/services/ask_stream.py) read this
    # to pick the fixed-response / record_tool-provenance behavior.
    rehydration_outcome: RehydrationResult = field(default_factory=NoReferenceContext)
    # The last persisted, normalized aggregate request/result is the sole
    # continuation handoff. It is never reconstructed from answer prose.
    aggregate_continuation: RecordAggregateContinuation | None = None

    def to_rag_input(self) -> RagTurnInput:
        return RagTurnInput(
            question=self.original_question,
            search_query=self.search_query,
            history=to_messages(self.history),
            record_context=self.record_context,
        )

    @property
    def records_only(self) -> bool:
        return self.policy is not None and self.policy.records_only


def _context_mode(page_context: TrustedPageContext | PageContextV2 | None) -> ContextMode | None:
    return "jobs" if page_context is not None else None


def _latest_aggregate_continuation(
    turns: list[TranscriptTurn],
) -> RecordAggregateContinuation | None:
    for turn in reversed(turns):
        if turn.aggregate_continuation is not None:
            return turn.aggregate_continuation
    return None


async def resolve_turn(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    loaded: LoadedThread | None = None,
) -> TurnContext:
    loaded = loaded or await load_thread(body, principal, resources=resources)

    if not settings.conversation_enabled:
        # The same precedence applies with conversations off -- a live
        # body.record_context is returned directly here, rather than
        # relying on a later ask_prepare.py overwrite.
        return TurnContext(
            thread_id=None,
            history=[],
            search_query=body.question,
            original_question=body.question,
            record_context=body.record_context,
            operation=body.operation,
            target_exchange_id=body.target_exchange_id,
            run_id=body.run_id,
        )

    thread_id = loaded.thread_id
    operation = loaded.operation
    target = loaded.target_exchange_id
    prompt_history = loaded.prompt_history

    aggregate_continuation = _latest_aggregate_continuation(prompt_history)

    # Explicit-context precedence, applied BEFORE rehydration and
    # condensation: (1) live body.record_context, (2) live body.page_context,
    # (3) rehydrated reference ledger, (4) no record context. An explicit
    # user selection is intentional and narrow; ambient page context is
    # incidental; a remembered ledger is the weakest signal of the three --
    # letting a lower-precedence source win could ground a confident answer
    # on the wrong records. Only a request with NEITHER live input may
    # rehydrate the nearest ledger: its typed identifiers feed BOTH the
    # condenser (pronoun resolution) and the answer prompt (via
    # TurnContext.record_context below) -- through the SAME fenced
    # record-context channel, never a second one (security-invariants.md
    # #1). `prompt_history` (not `full_history`) is the walk domain
    # deliberately: on regenerate it already excludes the turn being
    # replaced ("regenerate then follow-up uses the superseding artifact"
    # for the CURRENT request), and it is the same list condense_question
    # below already reads.
    if body.record_context is not None:
        record_context = body.record_context
        # Explicit NoReferenceContext() rather than relying on the field's
        # own default -- "a live record_context arrived this turn, so
        # rehydration was correctly skipped" is a deliberate DECISION at
        # this call site, not merely an unset field.
        rehydration_outcome = NoReferenceContext()
    elif body.page_context is not None:
        # A remembered record must never leak into a current-page question
        # -- skip ledger rehydration; ask_prepare consumes the compiled page
        # policy (records-only or ambient) instead.
        record_context = None
        rehydration_outcome = NoReferenceContext()
    else:
        rehydration_outcome = await rehydrate_for_followup(
            prompt_history, principal, resources=resources
        )
        record_context = record_context_from_rehydration(rehydration_outcome)

    if operation == "clarification_reply":
        # The business-query planner re-plans a clarification reply as
        # dialogue: it reads the original question plus the shown-question/
        # reply exchange as separate turns (business_query/wire/planning_round.py),
        # never this condensed rewrite. Condensing here is pure latency on the
        # tightest turn in the budget, and it can lose the original intent by
        # collapsing the question down to just the reply.
        search_query = body.question or ""
    else:
        try:
            search_query = await condense_question(body.question, prompt_history, record_context)
        except (RouteResolutionError, PolicyViolationError) as exc:
            rethrow_model_route_denial(exc)
        except Exception as exc:
            raise ServiceUnavailableError(f"conversation condense unavailable: {exc}") from exc

    return TurnContext(
        thread_id=thread_id,
        history=prompt_history,
        search_query=search_query,
        original_question=body.question,
        record_context=record_context,
        rehydration_outcome=rehydration_outcome,
        aggregate_continuation=aggregate_continuation,
        operation=operation,
        target_exchange_id=target,
        run_id=body.run_id,
        condensed=search_query != body.question,
    )


async def persist_exchange(
    thread_id: str,
    user_id: str | int,
    question: str,
    answer: str,
    *,
    exchange_id: str | None = None,
    context_mode: ContextMode | None = None,
    operation: str = "ask",
    target_exchange_id: str | None = None,
    reference_artifact: ReferenceArtifact | None = None,
    aggregate_continuation: RecordAggregateContinuation | None = None,
    bq_digest: BqTurnDigest | None = None,
    entity_id: int | None = None,
    tool_results: ToolResultFields | None = None,
    answer_mode: Literal["explanation", "direct"] | None = None,
    source_exchange_ids: Sequence[str] = (),
    conversation_subject: str | None = None,
) -> str | None:
    """Best-effort transcript write. Returns exchange_id when stored.

    ``reference_artifact`` and ``bq_digest`` thread onto the assistant turn via
    ``build_exchange_turns``; on regenerate, the replacement turns (built
    fresh from this call's values, default None) entirely
    replace ``record.turns[:-2]`` -- the old pair's artifact/digest is dropped
    along with the rest of the old pair, never merged with the new one.

    ``entity_id`` is the minting/asserting principal's entity scope -- see
    ``app/conversation/transcript_store.py``'s module docstring for the
    exact match rule.
    """
    store = get_conversation_store()
    turns = await run_in_thread(
        build_exchange_turns,
        question,
        answer,
        exchange_id=exchange_id,
        context_mode=context_mode,
        reference_artifact=reference_artifact,
        aggregate_continuation=aggregate_continuation,
        bq_digest=bq_digest,
        tool_results=tool_results,
        answer_mode=answer_mode,
        source_exchange_ids=tuple(source_exchange_ids),
        conversation_subject=conversation_subject,
    )
    eid = turns[0].exchange_id
    try:
        if operation == "regenerate" and target_exchange_id:
            await store.replace_latest_exchange(
                thread_id,
                user_id,
                target_exchange_id,
                question,
                context_mode,
                turns,
                entity_id=entity_id,
            )
        else:
            await store.append(thread_id, user_id, turns, entity_id=entity_id)
        return eid
    except RegenerateConflictError:
        raise
    except Exception:
        logger.exception(
            "transcript write failed thread=%s (answer already sent)",
            conversation_thread_hash(thread_id),
        )
        return None


async def finalize_exchange(
    ctx: TurnContext,
    principal: Principal,
    question: str,
    answer_text: str,
    *,
    reference_artifact: ReferenceArtifact | None = None,
    aggregate_continuation: RecordAggregateContinuation | None = None,
    bq_digest: BqTurnDigest | None = None,
    exchange_id: str | None = None,
    tool_results: ToolResultFields | None = None,
    answer_mode: Literal["explanation", "direct"] | None = None,
    source_exchange_ids: Sequence[str] = (),
    conversation_subject: str | None = None,
) -> PersistedExchange:
    """Persist the user+assistant exchange when sessions are on."""
    if ctx.thread_id is None:
        return PersistedExchange(thread_id=None, exchange_id=None)

    mode = _context_mode(ctx.page_context)
    replace_id = ctx.target_exchange_id if ctx.operation == "regenerate" else None
    new_eid = exchange_id or new_exchange_id()

    try:
        eid = await persist_exchange(
            ctx.thread_id,
            principal.user_id,
            question,
            answer_text,
            exchange_id=new_eid,
            context_mode=mode,
            operation=ctx.operation,
            target_exchange_id=replace_id,
            reference_artifact=reference_artifact,
            aggregate_continuation=aggregate_continuation,
            bq_digest=bq_digest,
            entity_id=principal.entity_id,
            tool_results=tool_results,
            answer_mode=answer_mode,
            source_exchange_ids=source_exchange_ids,
            conversation_subject=conversation_subject,
        )
    except RegenerateConflictError:
        raise

    if eid is None:
        return PersistedExchange(thread_id=None, exchange_id=None)
    return PersistedExchange(thread_id=ctx.thread_id, exchange_id=eid)
