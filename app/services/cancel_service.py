"""Multi-instance-safe cancellation coordination for streaming /api/ask runs.

Registers active generation tasks in Redis so POST /api/cancel on any worker can
reach the owning instance. Early cancels (before registration) are preserved via
short-lived tombstones; producers poll ``is_cancelled`` as a delivery fallback when
pub/sub is missed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import redis.asyncio as aioredis
from redis.exceptions import WatchError

from app.config import settings
from app.core.process_state import register_resettable

if TYPE_CHECKING:
    from app.auth import Principal

logger = logging.getLogger(__name__)

RUN_KEY_PREFIX = "ask:run:"
TOMBSTONE_KEY_PREFIX = "ask:run:tombstone:"
CANCEL_CHANNEL_PREFIX = "ask:cancel:"

RUN_KEY_TTL_SECONDS = 120
TOMBSTONE_TTL_SECONDS = 120
MAX_REDIS_CANCEL_POLLS = 120

_local_runs: dict[str, _LocalRegistration] = {}
_listener_task: asyncio.Task[None] | None = None
_listener_lock = asyncio.Lock()
_service_singleton: CancelService | None = None


def _instance_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _run_key(run_id: str) -> str:
    return f"{RUN_KEY_PREFIX}{run_id}"


def _tombstone_key(run_id: str) -> str:
    return f"{TOMBSTONE_KEY_PREFIX}{run_id}"


def _cancel_channel(instance_id: str) -> str:
    return f"{CANCEL_CHANNEL_PREFIX}{instance_id}"


def _principal_id(principal: str | int | Principal) -> str:
    if hasattr(principal, "user_id"):
        return str(principal.user_id)  # type: ignore[union-attr]
    return str(principal)


@dataclass
class _LocalRegistration:
    user_id: str
    task: asyncio.Task[object]
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    redis_polls: int = 0
    phase: str = "generating"


def _apply_local_cancel(run_id: str) -> None:
    reg = _local_runs.get(run_id)
    if reg is None or reg.cancelled.is_set():
        return
    reg.cancelled.set()
    # Once finalization starts, completion wins the race: the atomic transcript
    # commit and terminal done frame must remain one indivisible outcome.
    if reg.phase == "generating" and not reg.task.done():
        reg.task.cancel()


class CancelService(Protocol):
    async def register(
        self, run_id: str, principal: str | int | Principal, task: asyncio.Task[object]
    ) -> None: ...

    async def unregister(self, run_id: str) -> None: ...

    async def cancel_run(self, run_id: str, principal: str | int | Principal) -> bool: ...

    async def is_cancelled(self, run_id: str) -> bool: ...

    async def begin_finalize(self, run_id: str) -> bool: ...

    async def mark_done(self, run_id: str) -> None: ...

    @asynccontextmanager
    async def register_run(
        self, run_id: str, principal: str | int | Principal, task: asyncio.Task[object]
    ): ...


class InMemoryCancelService:
    """Process-local cancel registry — tests and single-worker dev only."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, object]] = {}
        self._tombstones: dict[str, str] = {}

    async def register(
        self, run_id: str, principal: str | int | Principal, task: asyncio.Task[object]
    ) -> None:
        user_id = _principal_id(principal)
        pre_cancelled = self._tombstones.get(run_id) == user_id
        if pre_cancelled:
            self._tombstones.pop(run_id, None)

        self._runs[run_id] = {
            "user_id": user_id,
            "instance_id": _instance_id(),
            "cancelled": pre_cancelled,
        }
        _local_runs[run_id] = _LocalRegistration(user_id=user_id, task=task)
        if pre_cancelled:
            # Registration itself must not inject cancellation into its caller
            # before the producer can enter its cleanup boundary.
            _local_runs[run_id].cancelled.set()

    async def unregister(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
        self._tombstones.pop(run_id, None)
        _local_runs.pop(run_id, None)

    async def cancel_run(self, run_id: str, principal: str | int | Principal) -> bool:
        user_id = _principal_id(principal)
        record = self._runs.get(run_id)
        if record is None:
            self._tombstones[run_id] = user_id
            return True

        if record["user_id"] != user_id:
            return False

        if record.get("cancelled"):
            return True

        record["cancelled"] = True
        _apply_local_cancel(run_id)
        return True

    async def is_cancelled(self, run_id: str) -> bool:
        reg = _local_runs.get(run_id)
        if reg is None:
            return False
        if reg.cancelled.is_set():
            return True
        if reg.redis_polls >= MAX_REDIS_CANCEL_POLLS:
            return False

        reg.redis_polls += 1
        record = self._runs.get(run_id)
        if record and record.get("cancelled"):
            reg.cancelled.set()
            return True
        return False

    async def begin_finalize(self, run_id: str) -> bool:
        reg = _local_runs.get(run_id)
        if reg is None or reg.cancelled.is_set():
            return False
        reg.phase = "finalizing"
        record = self._runs.get(run_id)
        if record is not None:
            record["phase"] = "finalizing"
        return True

    async def mark_done(self, run_id: str) -> None:
        reg = _local_runs.get(run_id)
        if reg is not None:
            reg.phase = "done"
        record = self._runs.get(run_id)
        if record is not None:
            record["phase"] = "done"

    @asynccontextmanager
    async def register_run(
        self, run_id: str, principal: str | int | Principal, task: asyncio.Task[object]
    ):
        await self.register(run_id, principal, task)
        try:
            yield
        finally:
            await self.unregister(run_id)


class RedisCancelService:
    """Multi-instance-safe cancel registry backed by Redis pub/sub + flag polling."""

    def __init__(
        self,
        redis_url: str,
        *,
        run_ttl_seconds: int = RUN_KEY_TTL_SECONDS,
        tombstone_ttl_seconds: int = TOMBSTONE_TTL_SECONDS,
        instance_id: str | None = None,
    ) -> None:
        self._instance_id = instance_id or _instance_id()
        self._run_ttl = run_ttl_seconds
        self._tombstone_ttl = tombstone_ttl_seconds
        self._redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            health_check_interval=30,
            max_connections=20,
        )
        self._pubsub_redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            health_check_interval=30,
            max_connections=5,
        )

    @staticmethod
    def _encode(
        user_id: str, instance_id: str, *, cancelled: bool, phase: str = "generating"
    ) -> str:
        return json.dumps(
            {
                "user_id": user_id,
                "instance_id": instance_id,
                "cancelled": cancelled,
                "phase": phase,
            }
        )

    @staticmethod
    def _decode(raw: str) -> dict[str, object]:
        return json.loads(raw)

    async def _ensure_listener(self) -> None:
        global _listener_task

        async with _listener_lock:
            if _listener_task is not None and not _listener_task.done():
                return
            _listener_task = asyncio.create_task(
                self._listen_for_cancellations(),
                name="ask-cancel-pubsub-listener",
            )

    async def _listen_for_cancellations(self) -> None:
        channel = _cancel_channel(self._instance_id)
        pubsub = self._pubsub_redis.pubsub()
        await pubsub.subscribe(channel)
        logger.debug("cancel pubsub subscribed channel=%s", channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                run_id = message.get("data")
                if isinstance(run_id, str) and run_id:
                    _apply_local_cancel(run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cancel pubsub listener failed channel=%s", channel)
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                logger.debug("cancel pubsub cleanup failed channel=%s", channel, exc_info=True)

    async def register(
        self, run_id: str, principal: str | int | Principal, task: asyncio.Task[object]
    ) -> None:
        user_id = _principal_id(principal)
        await self._ensure_listener()

        tombstone_user = await self._redis.get(_tombstone_key(run_id))
        pre_cancelled = tombstone_user is not None and tombstone_user == user_id
        if pre_cancelled:
            await self._redis.delete(_tombstone_key(run_id))

        await self._redis.set(
            _run_key(run_id),
            self._encode(user_id, self._instance_id, cancelled=pre_cancelled),
            ex=self._run_ttl,
        )

        _local_runs[run_id] = _LocalRegistration(user_id=user_id, task=task)
        if pre_cancelled:
            _local_runs[run_id].cancelled.set()
            await self._redis.set(
                _run_key(run_id),
                self._encode(user_id, self._instance_id, cancelled=True),
                ex=self._run_ttl,
            )

    async def unregister(self, run_id: str) -> None:
        _local_runs.pop(run_id, None)
        try:
            await self._redis.delete(_run_key(run_id))
        except Exception:
            logger.debug("cancel unregister delete failed run_id=%s", run_id, exc_info=True)

    async def cancel_run(self, run_id: str, principal: str | int | Principal) -> bool:
        user_id = _principal_id(principal)
        raw = await self._redis.get(_run_key(run_id))
        if raw is None:
            await self._redis.set(_tombstone_key(run_id), user_id, ex=self._tombstone_ttl)
            return True

        record = self._decode(raw)
        if str(record["user_id"]) != user_id:
            return False

        if bool(record.get("cancelled")):
            if str(record["instance_id"]) == self._instance_id:
                _apply_local_cancel(run_id)
            return True

        record["cancelled"] = True
        await self._redis.set(
            _run_key(run_id),
            json.dumps(record),
            ex=self._run_ttl,
        )

        owner_instance = str(record["instance_id"])
        if owner_instance == self._instance_id:
            _apply_local_cancel(run_id)
        elif record.get("phase") == "generating":
            await self._redis.publish(_cancel_channel(owner_instance), run_id)

        return True

    async def is_cancelled(self, run_id: str) -> bool:
        reg = _local_runs.get(run_id)
        if reg is None:
            return False
        if reg.cancelled.is_set():
            return True
        if reg.redis_polls >= MAX_REDIS_CANCEL_POLLS:
            return False

        reg.redis_polls += 1
        try:
            raw = await self._redis.get(_run_key(run_id))
        except Exception:
            logger.debug("cancel is_cancelled redis get failed run_id=%s", run_id, exc_info=True)
            return False

        if raw is None:
            return False

        if bool(self._decode(raw).get("cancelled")):
            reg.cancelled.set()
            _apply_local_cancel(run_id)
            return True
        return False

    async def begin_finalize(self, run_id: str) -> bool:
        reg = _local_runs.get(run_id)
        if reg is None or reg.cancelled.is_set():
            return False
        # Set the local phase before awaiting Redis so a simultaneous pub/sub
        # delivery cannot cancel a task that has entered the commit boundary.
        reg.phase = "finalizing"
        key = _run_key(run_id)
        for _attempt in range(5):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        reg.phase = "generating"
                        return False
                    record = self._decode(raw)
                    if bool(record.get("cancelled")):
                        reg.cancelled.set()
                        reg.phase = "generating"
                        return False
                    record["phase"] = "finalizing"
                    pipe.multi()
                    pipe.set(key, json.dumps(record), ex=self._run_ttl)
                    await pipe.execute()
                    return True
            except WatchError:
                continue
        reg.phase = "generating"
        return False

    async def mark_done(self, run_id: str) -> None:
        reg = _local_runs.get(run_id)
        if reg is not None:
            reg.phase = "done"
        key = _run_key(run_id)
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return
            record = self._decode(raw)
            record["phase"] = "done"
            await self._redis.set(key, json.dumps(record), ex=self._run_ttl)
        except Exception:
            logger.debug("cancel mark_done failed run_id=%s", run_id, exc_info=True)

    @asynccontextmanager
    async def register_run(
        self, run_id: str, principal: str | int | Principal, task: asyncio.Task[object]
    ):
        await self.register(run_id, principal, task)
        try:
            yield
        finally:
            await self.unregister(run_id)


def get_cancel_service() -> CancelService:
    global _service_singleton

    if _service_singleton is None:
        if settings.redis_url:
            _service_singleton = RedisCancelService(settings.redis_url)
        else:
            _service_singleton = InMemoryCancelService()
    return _service_singleton


def reset_cancel_service() -> None:
    """Clear the process-wide cancel singleton and local registrations between tests."""

    global _service_singleton, _listener_task

    _local_runs.clear()
    if _listener_task is not None and not _listener_task.done():
        _listener_task.cancel()
    _listener_task = None
    _service_singleton = None


async def cancel_run(run_id: str, principal: str | int | Principal) -> bool:
    return await get_cancel_service().cancel_run(run_id, principal)


@asynccontextmanager
async def register_run(run_id: str, principal: str | int | Principal, task: asyncio.Task[object]):
    async with get_cancel_service().register_run(run_id, principal, task):
        yield


async def is_cancelled(run_id: str) -> bool:
    return await get_cancel_service().is_cancelled(run_id)


async def begin_finalize(run_id: str) -> bool:
    return await get_cancel_service().begin_finalize(run_id)


async def mark_done(run_id: str) -> None:
    await get_cancel_service().mark_done(run_id)


register_resettable(reset_cancel_service)
