"""Thread loading extracted from resolve_turn (Task R3)."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from redis.exceptions import ConnectionError, RedisError

from app.config import settings
from app.conversation.policy import HistoryPolicy
from app.conversation.transcript_models import TranscriptTurn
from app.conversation.transcript_store import (
    ConversationStore,
    ThreadOwnershipError,
    get_conversation_store,
    new_thread_id,
)
from app.core.errors import (
    ConversationStoreUnavailableError,
    RegenerateConflictError,
)
from app.telemetry.helpers import conversation_thread_hash

if TYPE_CHECKING:
    from app.auth import Principal
    from app.models.schemas import Question
    from app.resources import ProcessResources

logger = logging.getLogger(__name__)

_REAL_GET_CONVERSATION_STORE = get_conversation_store
_REAL_NEW_THREAD_ID = new_thread_id


@dataclass(frozen=True)
class LoadedThread:
    thread_id: str | None
    full_history: list[TranscriptTurn]
    prompt_history: list[TranscriptTurn]  # capped by HistoryPolicy, regenerate target removed
    operation: str
    target_exchange_id: str | None
    regenerate_target: TranscriptTurn | None = None  # captured before removing the target
    regenerate_question: str | None = None  # paired original user text


def _current_store() -> ConversationStore:
    if get_conversation_store is not _REAL_GET_CONVERSATION_STORE:
        return get_conversation_store()
    turn_mod = sys.modules.get("app.conversation.turn")
    if turn_mod is not None:
        turn_getter = getattr(turn_mod, "get_conversation_store", None)
        if turn_getter is not None and turn_getter is not _REAL_GET_CONVERSATION_STORE:
            return turn_getter()
    return get_conversation_store()


def _current_settings():
    turn_mod = sys.modules.get("app.conversation.turn")
    if turn_mod is not None:
        turn_settings = getattr(turn_mod, "settings", None)
        if turn_settings is not None and turn_settings is not settings:
            return turn_settings
    return settings


def _current_new_thread_id() -> str:
    if new_thread_id is not _REAL_NEW_THREAD_ID:
        return new_thread_id()
    turn_mod = sys.modules.get("app.conversation.turn")
    if turn_mod is not None:
        fn = getattr(turn_mod, "new_thread_id", None)
        if fn is not None and fn is not _REAL_NEW_THREAD_ID:
            return fn()
    return new_thread_id()


def _resolve_thread_id(thread_id: str | None) -> str:
    if thread_id:
        return thread_id
    return _current_new_thread_id()


def _latest_exchange_id(turns: list[TranscriptTurn]) -> str | None:
    for turn in reversed(turns):
        if turn.exchange_id:
            return turn.exchange_id
    return None


def _history_without_exchange(
    turns: list[TranscriptTurn], exchange_id: str
) -> list[TranscriptTurn]:
    return [turn for turn in turns if turn.exchange_id != exchange_id]


async def _load_history(
    store: ConversationStore,
    thread_id: str | None,
    user_id: str | int,
    entity_id: int | None = None,
) -> list[TranscriptTurn]:
    if not thread_id:
        return []
    return await store.load(thread_id, user_id, entity_id)


async def load_thread(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources | None = None,
    store: ConversationStore | None = None,
) -> LoadedThread:
    effective_settings = _current_settings()
    if not effective_settings.conversation_enabled:
        return LoadedThread(
            thread_id=None,
            full_history=[],
            prompt_history=[],
            operation=body.operation,
            target_exchange_id=body.target_exchange_id,
        )

    if store is None:
        store = _current_store()
    try:
        if not await store.ping():
            raise RedisError("conversation store readiness check failed")
        full_history = await _load_history(
            store, body.thread_id, principal.user_id, principal.entity_id
        )
    except ThreadOwnershipError:
        rejected = body.thread_id
        if rejected is None:
            raise
        logger.warning(
            "thread ownership rejected thread=%s",
            conversation_thread_hash(rejected),
        )
        full_history = []
        thread_id = _current_new_thread_id()
    except (RedisError, ConnectionError) as exc:
        raise ConversationStoreUnavailableError(f"conversation store unavailable: {exc}") from exc
    else:
        thread_id = _resolve_thread_id(body.thread_id)

    operation = body.operation
    target = body.target_exchange_id
    prompt_history = HistoryPolicy.current().cap_for_prompt(full_history)

    regenerate_target: TranscriptTurn | None = None
    regenerate_question: str | None = None

    if operation == "regenerate":
        if not target:
            raise RegenerateConflictError("missing target_exchange_id")
        if not full_history:
            raise RegenerateConflictError("thread missing")
        latest = _latest_exchange_id(full_history)
        if latest != target:
            raise RegenerateConflictError("target exchange is not the latest pair")

        for turn in reversed(full_history):
            if turn.exchange_id == target:
                if turn.role == "assistant" and regenerate_target is None:
                    regenerate_target = turn
                elif turn.role == "user" and regenerate_question is None:
                    regenerate_question = turn.content

        prompt_history = _history_without_exchange(prompt_history, target)

    return LoadedThread(
        thread_id=thread_id,
        full_history=full_history,
        prompt_history=prompt_history,
        operation=operation,
        target_exchange_id=target,
        regenerate_target=regenerate_target,
        regenerate_question=regenerate_question,
    )
