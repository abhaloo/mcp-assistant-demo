"""Server-side, session-scoped conversation transcript store.

ADR 0018 / ADR 0071: plain-Python business logic at the service boundary. This store
holds the conversation transcript and pending continuations; loop checkpoints live in
the query_records agent_checkpoints tables, never here. ADR 0014: a thread is bound to
its creator's user_id; cross-user access raises ThreadOwnershipError.

Writes reset TTL (sliding session expiry). Reads do not extend TTL — idle threads expire
even if the client still holds the id.

Entity-scoped ownership (task A7a, R5): a thread is ALSO bound to the
minting principal's ``entity_id`` (``None`` for a v1/claimless principal),
matched exactly on every subsequent load/append/replace — same mechanism as
the user_id check above, and it raises the SAME ``ThreadOwnershipError``
(no new error shape; ``app.conversation.turn._load_history`` already
converts that into an empty history, never a leaked "thread exists" signal).
``None`` matches ``None`` only (a legacy thread minted before this field
existed, or under a v1 token, stays readable ONLY while no entity scope is
asserted; once either side asserts a real entity_id, it fails closed rather
than guessing). An entity switch between requests — the same user_id, a
different entity_id — is therefore indistinguishable from cross-user access
or TTL expiry from the caller's point of view: a fresh empty thread, never
an error. ``cross_entity`` principals are NOT special-cased here: they are
scoped to their own ``entity_id`` exactly like any other principal — that
flag only affects ``PolicyScopedRecordExecutor``'s row-level SQL scoping
(app/policy/record_executor.py), an unrelated concern this store never
reads. Permission/capability changes short of an entity switch do not
reach this check at all (this module only ever sees ``entity_id`` — a
per-request re-authorization of what a principal is *permitted* to do is
A7b's job, not thread-ownership's).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import redis.asyncio as aioredis
from redis.exceptions import RedisError, WatchError

from app.config import settings
from app.conversation.policy import HistoryPolicy
from app.conversation.transcript_models import ContextMode, TranscriptTurn
from app.core.errors import (
    ConversationStoreUnavailableError,
    RegenerateConflictError,
    TranscriptStoreContentionError,
)
from app.core.process_state import register_resettable

REDIS_APPEND_MAX_RETRIES = 10


def _now() -> float:
    return time.time()


class ThreadOwnershipError(Exception):
    """Raised when a thread is accessed by a user_id other than its creator."""


# A claim must outlive the longest Ask turn (25 s) plus store round trips, or a
# same-key retry that arrives while the turn is still running fails it.
CONTINUATION_LEASE_SECONDS = 60

# "reclaimed": the execution that holds the claim asked again. "in_progress": another
# execution asked with the same idempotency key while the claim is live.
ContinuationClaimStatus = Literal["claimed", "reclaimed", "in_progress", "completed", "rejected"]


@dataclass(frozen=True)
class ContinuationClaim:
    status: ContinuationClaimStatus
    answer: dict[str, Any] | None = None


@dataclass(frozen=True)
class ContinuationJoinResult:
    status: Literal[
        "completed", "mismatch", "conflict", "expired", "continuation_unavailable", "failed"
    ]
    execution_id: str | None = None
    answer: dict[str, Any] | None = None
    aqid: str | None = None
    terminal_digest: str | None = None


def new_thread_id() -> str:
    return uuid.uuid4().hex


def new_exchange_id() -> str:
    return uuid.uuid4().hex


@dataclass
class ThreadRecord:
    owner: str
    turns: list[TranscriptTurn] = field(default_factory=list)
    created_at: float = field(default_factory=_now)
    # Entity-scoped ownership (task A7a, R5) — recorded from the minting
    # principal's entity_id; None for a v1/claimless principal or a legacy
    # thread predating this field. See module docstring for the match rule.
    entity_id: int | None = None


def _record_expired(record: ThreadRecord) -> bool:
    if record.created_at <= 0:
        return True
    return (time.time() - record.created_at) > settings.conversation_absolute_ttl_seconds


def _verify_regenerate_tail(
    record: ThreadRecord,
    target_exchange_id: str,
    expected_question: str,
    expected_context_mode: ContextMode | None,
) -> None:
    turns = record.turns
    if len(turns) < 2:
        raise RegenerateConflictError("tail too short")
    user_turn, assistant_turn = turns[-2], turns[-1]
    if user_turn.role != "user" or assistant_turn.role != "assistant":
        raise RegenerateConflictError("tail is not a user+assistant pair")
    if (
        user_turn.exchange_id != target_exchange_id
        or assistant_turn.exchange_id != target_exchange_id
    ):
        raise RegenerateConflictError("target exchange is not the latest pair")
    if user_turn.exchange_id is None or assistant_turn.exchange_id is None:
        raise RegenerateConflictError("legacy tail without exchange_id")
    if user_turn.content != expected_question:
        raise RegenerateConflictError("expected question mismatch")
    if user_turn.context_mode != expected_context_mode:
        raise RegenerateConflictError("context mode mismatch")


class ConversationStore(Protocol):
    async def ping(self) -> bool: ...

    async def load(
        self, thread_id: str, user_id: str | int, entity_id: int | None = None
    ) -> list[TranscriptTurn]: ...

    async def append(
        self,
        thread_id: str,
        user_id: str | int,
        turns: list[TranscriptTurn],
        entity_id: int | None = None,
    ) -> None: ...

    async def replace_latest_exchange(
        self,
        thread_id: str,
        user_id: str | int,
        target_exchange_id: str,
        expected_question: str,
        expected_context_mode: ContextMode | None,
        turns: list[TranscriptTurn],
        entity_id: int | None = None,
    ) -> None: ...

    async def claim_continuation(
        self, jti: str, request_id: str, *, expires_at: int
    ) -> ContinuationClaim: ...

    async def complete_continuation(
        self, jti: str, request_id: str, answer: dict[str, Any]
    ) -> None: ...

    async def create_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        thread_id: str | None,
        principal_binding_hash: str,
        intent_hash: str,
        pending_blob: dict[str, Any],
        expires_at: int,
        question: str = "",
    ) -> None: ...

    async def claim_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        idempotency_key_hash: str,
        principal_binding_hash: str,
        thread_id: str | None,
        lease_seconds: int = CONTINUATION_LEASE_SECONDS,
    ) -> ContinuationClaim: ...

    async def complete_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        terminal_outcome: dict[str, Any],
        aqid: str | None = None,
        digest: str | None = None,
    ) -> None: ...

    async def fail_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        error_detail: str | None = None,
    ) -> None: ...

    async def get_continuation_record(self, jti: str) -> dict[str, Any] | None: ...

    async def wait_continuation_completion(
        self,
        jti: str,
        *,
        timeout_seconds: float,
        join_redis: Any | None = None,
    ) -> dict[str, Any] | None: ...


class InMemoryConversationStore:
    """Process-local store — tests and single-process dev only."""

    def __init__(self, *, policy: HistoryPolicy | None = None) -> None:
        self._policy = policy or HistoryPolicy.current()
        self._records: dict[str, ThreadRecord] = {}
        self._continuations: dict[str, dict[str, Any]] = {}
        self._continuation_events: dict[str, asyncio.Event] = {}

    async def ping(self) -> bool:
        """Match the Redis store's readiness seam for local/test storage."""
        return True

    def _check_owner(
        self, thread_id: str, user_id: str | int, entity_id: int | None = None
    ) -> None:
        record = self._records.get(thread_id)
        if record is None:
            return
        if _record_expired(record):
            self._records.pop(thread_id, None)
            return
        if record.owner != str(user_id) or record.entity_id != entity_id:
            raise ThreadOwnershipError(thread_id)

    async def load(
        self, thread_id: str, user_id: str | int, entity_id: int | None = None
    ) -> list[TranscriptTurn]:
        record = self._records.get(thread_id)
        if record is None:
            return []
        if _record_expired(record):
            self._records.pop(thread_id, None)
            return []
        self._check_owner(thread_id, user_id, entity_id)
        return list(record.turns)

    async def append(
        self,
        thread_id: str,
        user_id: str | int,
        turns: list[TranscriptTurn],
        entity_id: int | None = None,
    ) -> None:
        self._check_owner(thread_id, user_id, entity_id)
        record = self._records.setdefault(
            thread_id, ThreadRecord(owner=str(user_id), entity_id=entity_id)
        )
        record.turns = self._policy.trim_for_storage(record.turns + list(turns))

    async def replace_latest_exchange(
        self,
        thread_id: str,
        user_id: str | int,
        target_exchange_id: str,
        expected_question: str,
        expected_context_mode: ContextMode | None,
        turns: list[TranscriptTurn],
        entity_id: int | None = None,
    ) -> None:
        self._check_owner(thread_id, user_id, entity_id)
        record = self._records.get(thread_id)
        if record is None:
            raise RegenerateConflictError("thread missing")
        _verify_regenerate_tail(
            record, target_exchange_id, expected_question, expected_context_mode
        )
        record.turns = self._policy.trim_for_storage(record.turns[:-2] + list(turns))

    async def claim_continuation(
        self, jti: str, request_id: str, *, expires_at: int
    ) -> ContinuationClaim:
        state = self._live_continuation(jti)
        if state is None:
            self._continuations[jti] = {
                "request_id": request_id,
                "status": "claimed",
                "expires_at": expires_at,
            }
            return ContinuationClaim("claimed")
        if state.get("request_id") != request_id:
            return ContinuationClaim("rejected")
        if state.get("status") == "completed":
            return ContinuationClaim("completed", state.get("answer"))
        return ContinuationClaim("in_progress")

    async def complete_continuation(
        self, jti: str, request_id: str, answer: dict[str, Any]
    ) -> None:
        state = self._continuations.get(jti)
        if state is None or state.get("request_id") != request_id:
            return
        state["status"] = "completed"
        state["answer"] = answer

    async def create_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        thread_id: str | None,
        principal_binding_hash: str,
        intent_hash: str,
        pending_blob: dict[str, Any],
        expires_at: int,
        question: str = "",
    ) -> None:
        self._continuations[jti] = {
            "status": "PENDING",
            "execution_id": execution_id,
            "thread_id": thread_id,
            "principal_binding_hash": principal_binding_hash,
            "intent_hash": intent_hash,
            "pending_blob": pending_blob,
            "question": question,
            "expires_at": expires_at,
            "created_at": time.time(),
            "lease_until": None,
            "idempotency_key_hash": None,
            "terminal_outcome": None,
            "terminal_aqid": None,
            "terminal_digest": None,
        }
        self._continuation_events[jti] = asyncio.Event()

    def _live_continuation(self, jti: str) -> dict[str, Any] | None:
        state = self._continuations.get(jti)
        if state is None:
            return None
        if time.time() > state["expires_at"]:
            self._continuations.pop(jti, None)
            self._continuation_events.pop(jti, None)
            return None
        return state

    async def claim_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        idempotency_key_hash: str,
        principal_binding_hash: str,
        thread_id: str | None,
        lease_seconds: int = CONTINUATION_LEASE_SECONDS,
    ) -> ContinuationClaim:
        state = self._live_continuation(jti)
        if state is None:
            return ContinuationClaim("rejected")
        if state["principal_binding_hash"] != principal_binding_hash:
            return ContinuationClaim("rejected")
        if state.get("thread_id") is not None and state["thread_id"] != thread_id:
            return ContinuationClaim("rejected")

        cur_status = state["status"]
        if cur_status == "PENDING":
            state["status"] = "CLAIMED"
            state["idempotency_key_hash"] = idempotency_key_hash
            state["claimed_by"] = execution_id
            state["lease_until"] = time.time() + lease_seconds
            return ContinuationClaim("claimed")
        if cur_status == "CLAIMED":
            if time.time() > (state.get("lease_until") or 0):
                state["status"] = "TERMINAL_FAILED"
                state["error_detail"] = "lease_expired"
                return ContinuationClaim("rejected")
            if state.get("idempotency_key_hash") != idempotency_key_hash:
                return ContinuationClaim("rejected")
            if state.get("claimed_by") == execution_id:
                return ContinuationClaim("reclaimed")
            return ContinuationClaim("in_progress")
        if cur_status == "COMPLETED":
            if state.get("idempotency_key_hash") == idempotency_key_hash:
                return ContinuationClaim("completed", state.get("terminal_outcome"))
            return ContinuationClaim("rejected")
        if cur_status == "TERMINAL_FAILED":
            return ContinuationClaim("rejected")
        return ContinuationClaim("rejected")

    async def complete_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        terminal_outcome: dict[str, Any],
        aqid: str | None = None,
        digest: str | None = None,
    ) -> None:
        state = self._continuations.get(jti)
        if state is None or state["execution_id"] != execution_id:
            return
        if state["status"] != "CLAIMED":
            return
        state["status"] = "COMPLETED"
        state["terminal_outcome"] = terminal_outcome
        state["terminal_aqid"] = aqid
        state["terminal_digest"] = digest
        event = self._continuation_events.get(jti)
        if event is not None:
            event.set()

    async def fail_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        error_detail: str | None = None,
    ) -> None:
        state = self._continuations.get(jti)
        if state is None or state["execution_id"] != execution_id:
            return
        if state["status"] != "CLAIMED":
            return
        state["status"] = "TERMINAL_FAILED"
        state["error_detail"] = error_detail
        event = self._continuation_events.get(jti)
        if event is not None:
            event.set()

    async def get_continuation_record(self, jti: str) -> dict[str, Any] | None:
        return self._live_continuation(jti)

    async def wait_continuation_completion(
        self,
        jti: str,
        *,
        timeout_seconds: float,
        join_redis: Any | None = None,
    ) -> dict[str, Any] | None:
        event = self._continuation_events.setdefault(jti, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=max(0.01, timeout_seconds))
        except TimeoutError:
            pass
        return self._continuations.get(jti)


class RedisConversationStore:
    """Multi-instance-safe transcript store. Trimming uses HistoryPolicy in Python."""

    def __init__(
        self,
        redis_url: str,
        *,
        policy: HistoryPolicy | None = None,
        ttl_seconds: int | None = None,
        max_append_retries: int = REDIS_APPEND_MAX_RETRIES,
    ) -> None:
        self._redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            health_check_interval=30,
            max_connections=20,
        )
        self._policy = policy or HistoryPolicy.current()
        self._ttl = ttl_seconds if ttl_seconds is not None else settings.conversation_ttl_seconds
        self._max_append_retries = max_append_retries

    @staticmethod
    def _key(thread_id: str) -> str:
        return f"conv:{thread_id}"

    @staticmethod
    def _continuation_key(jti: str) -> str:
        return f"continuation:{jti}"

    async def ping(self) -> bool:
        """Prove the transcript backend is reachable before issuing a thread id."""
        return bool(await self._redis.ping())

    def _record_from_raw(
        self,
        raw: str,
        user_id: str | int,
        *,
        thread_id: str,
        entity_id: int | None = None,
    ) -> ThreadRecord:
        data = json.loads(raw)
        if str(data["owner"]) != str(user_id):
            raise ThreadOwnershipError(thread_id)
        # Legacy records predating task A7a have no "entity_id" key at all --
        # .get(...) defaults to None, matching a caller asserting no entity
        # scope (see module docstring for the full match rule).
        stored_entity_id = data.get("entity_id")
        if stored_entity_id != entity_id:
            raise ThreadOwnershipError(thread_id)
        return ThreadRecord(
            owner=str(data["owner"]),
            turns=[TranscriptTurn(**t) for t in data["turns"]],
            created_at=float(data.get("created_at", 0.0)),
            entity_id=stored_entity_id,
        )

    def _encode(self, record: ThreadRecord) -> str:
        return json.dumps(
            {
                "owner": record.owner,
                "entity_id": record.entity_id,
                "turns": [t.model_dump(exclude_none=True) for t in record.turns],
                "created_at": record.created_at,
            }
        )

    async def load(
        self, thread_id: str, user_id: str | int, entity_id: int | None = None
    ) -> list[TranscriptTurn]:
        raw = await self._redis.get(self._key(thread_id))
        if raw is None:
            return []
        record = self._record_from_raw(raw, user_id, thread_id=thread_id, entity_id=entity_id)
        if _record_expired(record):
            return []
        return list(record.turns)

    async def append(
        self,
        thread_id: str,
        user_id: str | int,
        turns: list[TranscriptTurn],
        entity_id: int | None = None,
    ) -> None:
        key = self._key(thread_id)
        for attempt in range(self._max_append_retries):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await self._redis.get(key)
                    if raw is not None:
                        record = self._record_from_raw(
                            raw, user_id, thread_id=thread_id, entity_id=entity_id
                        )
                        if _record_expired(record):
                            record = ThreadRecord(owner=str(user_id), entity_id=entity_id)
                    else:
                        record = ThreadRecord(owner=str(user_id), entity_id=entity_id)
                    record.turns = self._policy.trim_for_storage(record.turns + list(turns))
                    pipe.multi()
                    pipe.set(key, self._encode(record), ex=self._ttl)
                    await pipe.execute()
                    return
            except WatchError:
                if attempt + 1 >= self._max_append_retries:
                    raise TranscriptStoreContentionError(
                        f"transcript append contention exhausted after "
                        f"{self._max_append_retries} retries"
                    ) from None
                continue

    async def replace_latest_exchange(
        self,
        thread_id: str,
        user_id: str | int,
        target_exchange_id: str,
        expected_question: str,
        expected_context_mode: ContextMode | None,
        turns: list[TranscriptTurn],
        entity_id: int | None = None,
    ) -> None:
        key = self._key(thread_id)
        for attempt in range(self._max_append_retries):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await self._redis.get(key)
                    if raw is None:
                        raise RegenerateConflictError("thread missing")
                    record = self._record_from_raw(
                        raw, user_id, thread_id=thread_id, entity_id=entity_id
                    )
                    _verify_regenerate_tail(
                        record, target_exchange_id, expected_question, expected_context_mode
                    )
                    record.turns = self._policy.trim_for_storage(record.turns[:-2] + list(turns))
                    pipe.multi()
                    pipe.set(key, self._encode(record), ex=self._ttl)
                    await pipe.execute()
                    return
            except WatchError:
                if attempt + 1 >= self._max_append_retries:
                    raise TranscriptStoreContentionError(
                        f"transcript replace contention exhausted after "
                        f"{self._max_append_retries} retries"
                    ) from None
                continue

    async def claim_continuation(
        self, jti: str, request_id: str, *, expires_at: int
    ) -> ContinuationClaim:
        key = self._continuation_key(jti)
        for attempt in range(self._max_append_retries):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await self._redis.get(key)
                    if raw is None:
                        state = {
                            "request_id": request_id,
                            "status": "claimed",
                            "expires_at": expires_at,
                        }
                        pipe.multi()
                        pipe.set(key, json.dumps(state), ex=max(1, expires_at - int(_now())))
                        await pipe.execute()
                        return ContinuationClaim("claimed")
                    state = json.loads(raw)
                    if state.get("request_id") != request_id:
                        return ContinuationClaim("rejected")
                    if state.get("status") == "completed":
                        return ContinuationClaim("completed", state.get("answer"))
                    return ContinuationClaim("in_progress")
            except WatchError:
                if attempt + 1 >= self._max_append_retries:
                    raise TranscriptStoreContentionError(
                        f"continuation claim contention exhausted after "
                        f"{self._max_append_retries} retries"
                    ) from None
                continue

    async def complete_continuation(
        self, jti: str, request_id: str, answer: dict[str, Any]
    ) -> None:
        key = self._continuation_key(jti)
        for attempt in range(self._max_append_retries):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await self._redis.get(key)
                    if raw is None:
                        return
                    state = json.loads(raw)
                    if state.get("request_id") != request_id:
                        return
                    state["status"] = "completed"
                    state["answer"] = answer
                    pipe.multi()
                    pipe.set(
                        key,
                        json.dumps(state),
                        ex=max(1, int(state.get("expires_at", _now())) - int(_now())),
                    )
                    await pipe.execute()
                    return
            except WatchError:
                if attempt + 1 >= self._max_append_retries:
                    raise TranscriptStoreContentionError(
                        f"continuation completion contention exhausted after "
                        f"{self._max_append_retries} retries"
                    ) from None
                continue

    @staticmethod
    def _sql_continuation_key(jti: str) -> str:
        return f"continuation:sql:{jti}"

    @staticmethod
    def _notify_key(jti: str) -> str:
        return f"continuation:notify:{jti}"

    @staticmethod
    def _raise_continuation_store_unavailable(operation: str, exc: RedisError) -> None:
        raise ConversationStoreUnavailableError(f"continuation {operation} failed: {exc}") from exc

    async def create_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        thread_id: str | None,
        principal_binding_hash: str,
        intent_hash: str,
        pending_blob: dict[str, Any],
        expires_at: int,
        question: str = "",
    ) -> None:
        key = self._sql_continuation_key(jti)
        state = {
            "status": "PENDING",
            "execution_id": execution_id,
            "thread_id": thread_id,
            "principal_binding_hash": principal_binding_hash,
            "intent_hash": intent_hash,
            "pending_blob": pending_blob,
            "question": question,
            "expires_at": expires_at,
            "created_at": _now(),
            "lease_until": None,
            "idempotency_key_hash": None,
            "terminal_outcome": None,
            "terminal_aqid": None,
            "terminal_digest": None,
        }
        ttl = max(1, expires_at - int(_now()))
        try:
            await self._redis.set(key, json.dumps(state), ex=ttl)
        except RedisError as exc:
            self._raise_continuation_store_unavailable("create", exc)

    async def claim_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        idempotency_key_hash: str,
        principal_binding_hash: str,
        thread_id: str | None,
        lease_seconds: int = CONTINUATION_LEASE_SECONDS,
    ) -> ContinuationClaim:
        key = self._sql_continuation_key(jti)
        for attempt in range(self._max_append_retries):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await self._redis.get(key)
                    if raw is None:
                        return ContinuationClaim("rejected")
                    state = json.loads(raw)
                    if _now() > state["expires_at"]:
                        return ContinuationClaim("rejected")
                    if state["principal_binding_hash"] != principal_binding_hash:
                        return ContinuationClaim("rejected")
                    if state.get("thread_id") is not None and state["thread_id"] != thread_id:
                        return ContinuationClaim("rejected")

                    cur_status = state["status"]
                    if cur_status == "PENDING":
                        state["status"] = "CLAIMED"
                        state["idempotency_key_hash"] = idempotency_key_hash
                        state["claimed_by"] = execution_id
                        state["lease_until"] = _now() + lease_seconds
                        ttl = max(1, int(state["expires_at"]) - int(_now()))
                        pipe.multi()
                        pipe.set(key, json.dumps(state), ex=ttl)
                        await pipe.execute()
                        return ContinuationClaim("claimed")
                    if cur_status == "CLAIMED":
                        if _now() > (state.get("lease_until") or 0):
                            state["status"] = "TERMINAL_FAILED"
                            state["error_detail"] = "lease_expired"
                            ttl = max(1, int(state["expires_at"]) - int(_now()))
                            pipe.multi()
                            pipe.set(key, json.dumps(state), ex=ttl)
                            await pipe.execute()
                            return ContinuationClaim("rejected")
                        if state.get("idempotency_key_hash") != idempotency_key_hash:
                            return ContinuationClaim("rejected")
                        if state.get("claimed_by") == execution_id:
                            return ContinuationClaim("reclaimed")
                        return ContinuationClaim("in_progress")
                    if cur_status == "COMPLETED":
                        if state.get("idempotency_key_hash") == idempotency_key_hash:
                            return ContinuationClaim("completed", state.get("terminal_outcome"))
                        return ContinuationClaim("rejected")
                    if cur_status == "TERMINAL_FAILED":
                        return ContinuationClaim("rejected")
                    return ContinuationClaim("rejected")
            except RedisError as exc:
                self._raise_continuation_store_unavailable("claim", exc)
            except WatchError:
                if attempt + 1 >= self._max_append_retries:
                    raise TranscriptStoreContentionError(
                        f"continuation claim contention exhausted after "
                        f"{self._max_append_retries} retries"
                    ) from None
                continue

    async def complete_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        terminal_outcome: dict[str, Any],
        aqid: str | None = None,
        digest: str | None = None,
    ) -> None:
        key = self._sql_continuation_key(jti)
        for attempt in range(self._max_append_retries):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await self._redis.get(key)
                    if raw is None:
                        return
                    state = json.loads(raw)
                    if state.get("execution_id") != execution_id:
                        return
                    if state.get("status") != "CLAIMED":
                        return
                    state["status"] = "COMPLETED"
                    state["terminal_outcome"] = terminal_outcome
                    state["terminal_aqid"] = aqid
                    state["terminal_digest"] = digest
                    ttl = max(1, int(state.get("expires_at", _now())) - int(_now()))
                    notify_key = self._notify_key(jti)
                    pipe.multi()
                    pipe.set(key, json.dumps(state), ex=ttl)
                    pipe.lpush(notify_key, "completed")
                    pipe.expire(notify_key, 60)
                    await pipe.execute()
                    return
            except RedisError as exc:
                self._raise_continuation_store_unavailable("complete", exc)
            except WatchError:
                if attempt + 1 >= self._max_append_retries:
                    raise TranscriptStoreContentionError(
                        f"continuation completion contention exhausted after "
                        f"{self._max_append_retries} retries"
                    ) from None
                continue

    async def fail_pending_continuation(
        self,
        jti: str,
        *,
        execution_id: str,
        error_detail: str | None = None,
    ) -> None:
        key = self._sql_continuation_key(jti)
        for attempt in range(self._max_append_retries):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await self._redis.get(key)
                    if raw is None:
                        return
                    state = json.loads(raw)
                    if state.get("execution_id") != execution_id:
                        return
                    if state.get("status") != "CLAIMED":
                        return
                    state["status"] = "TERMINAL_FAILED"
                    state["error_detail"] = error_detail
                    ttl = max(1, int(state.get("expires_at", _now())) - int(_now()))
                    notify_key = self._notify_key(jti)
                    pipe.multi()
                    pipe.set(key, json.dumps(state), ex=ttl)
                    pipe.lpush(notify_key, "failed")
                    pipe.expire(notify_key, 60)
                    await pipe.execute()
                    return
            except RedisError as exc:
                self._raise_continuation_store_unavailable("fail", exc)
            except WatchError:
                if attempt + 1 >= self._max_append_retries:
                    raise TranscriptStoreContentionError(
                        f"continuation failure contention exhausted after "
                        f"{self._max_append_retries} retries"
                    ) from None
                continue

    async def get_continuation_record(self, jti: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(self._sql_continuation_key(jti))
        except RedisError as exc:
            self._raise_continuation_store_unavailable("get", exc)
        if raw is None:
            return None
        return json.loads(raw)

    async def wait_continuation_completion(
        self,
        jti: str,
        *,
        timeout_seconds: float,
        join_redis: Any | None = None,
    ) -> dict[str, Any] | None:
        client = join_redis or self._redis
        notify_key = self._notify_key(jti)
        timeout_int = max(1, int(timeout_seconds))
        try:
            await client.blpop(notify_key, timeout=timeout_int)
        except RedisError as exc:
            raise ConversationStoreUnavailableError(
                f"continuation join wait failed: {exc}"
            ) from exc
        return await self.get_continuation_record(jti)


_store_singleton: ConversationStore | None = None


def get_conversation_store() -> ConversationStore:
    global _store_singleton
    if not settings.conversation_enabled:
        raise RuntimeError("conversation store is unavailable when conversation_enabled=false")
    if _store_singleton is None:
        _store_singleton = RedisConversationStore(settings.redis_url)
    return _store_singleton


def reset_conversation_store() -> None:
    global _store_singleton
    _store_singleton = None


register_resettable(reset_conversation_store)
