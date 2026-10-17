"""One-time JWT ``jti`` consumption — replay protection at the service boundary."""

from __future__ import annotations

import logging
from typing import Protocol

import redis.asyncio as aioredis

from app.config import settings
from app.core.process_state import register_resettable

logger = logging.getLogger(__name__)

JTI_KEY_PREFIX = "jwt:jti:"
JTI_TTL_SECONDS = 60

_store_singleton: JtiReplayStore | None = None
_seen_jtis: set[str] = set()


class JtiReplayStore(Protocol):
    async def consume(self, jti: str) -> bool: ...


class InMemoryJtiReplayStore:
    """Process-local jti set — tests and single-worker dev only."""

    async def consume(self, jti: str) -> bool:
        if jti in _seen_jtis:
            return False
        _seen_jtis.add(jti)
        return True


class RedisJtiReplayStore:
    """Multi-instance-safe one-time jti consumption via SET NX."""

    def __init__(self, redis_url: str, *, ttl_seconds: int = JTI_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._redis = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
            health_check_interval=30,
            max_connections=10,
        )

    @staticmethod
    def _key(jti: str) -> str:
        return f"{JTI_KEY_PREFIX}{jti}"

    async def consume(self, jti: str) -> bool:
        try:
            created = await self._redis.set(self._key(jti), "1", nx=True, ex=self._ttl)
        except Exception:
            logger.exception("jti replay store unavailable")
            return False
        return bool(created)


def get_jti_replay_store() -> JtiReplayStore:
    global _store_singleton
    if _store_singleton is None:
        if settings.redis_url:
            _store_singleton = RedisJtiReplayStore(settings.redis_url)
        else:
            _store_singleton = InMemoryJtiReplayStore()
    return _store_singleton


def reset_jti_replay_store() -> None:
    global _store_singleton
    _seen_jtis.clear()
    _store_singleton = None


async def consume_jti(jti: str) -> bool:
    """Return True when ``jti`` is fresh; False on replay or store failure."""
    return await get_jti_replay_store().consume(jti)


register_resettable(reset_jti_replay_store)
