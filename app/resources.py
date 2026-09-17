"""Process resource ownership and shutdown.

The composition root builds one instance in the FastAPI lifespan and hands
focused resources to the modules that need them. Each slot builds on first
read, under its own lock, and never publishes a failed construction.

Lifecycle: ``OPEN`` → ``CLOSING`` → ``CLOSED``. Acquire only succeeds in
``OPEN``. Prefer ``await aclose()`` (lifespan and async fixtures); sync
``shutdown()`` drives the same path when no event loop is running.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, Token
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.core.bounded_executor import BoundedExecutor
from app.core.errors import CAPABILITY_UNAVAILABLE_MESSAGE, CapabilityUnavailableError

logger = logging.getLogger(__name__)

_SLOT_NAMES = (
    "record_engine",
    "policy_record_engine",
    "adapter_executor",
    "document_executor",
    "sync_http_client",
    "async_http_client",
    "document_sync_http_client",
    "document_async_http_client",
    "azure_credential",
    "query_record_postgres",
    "join_redis",
    "conversation_store",
)

_HTTP_LIMITS = httpx.Limits(
    max_connections=100, max_keepalive_connections=20, keepalive_expiry=30.0
)
_HTTP_TIMEOUT = httpx.Timeout(60.0)
_DOCUMENT_HTTP_TIMEOUT = httpx.Timeout(2.0)

DisposeFn = Callable[[Any], None | Awaitable[None]]

_bound_resources: ContextVar[ProcessResources | None] = ContextVar(
    "bound_process_resources", default=None
)
_global_process_resources: ProcessResources | None = None


class ProcessResourcesClosedError(RuntimeError):
    """Raised when a slot is acquired after shutdown has started or finished."""


class _Lifecycle(Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


def _dispose_engine(engine: Engine) -> None:
    engine.dispose()


def _shutdown_executor(executor: ThreadPoolExecutor) -> None:
    executor.shutdown(wait=True)


def _shutdown_document_executor(executor: BoundedExecutor) -> None:
    executor.shutdown(wait=True, cancel_futures=True)


def _dispose_sync_http_client(client: httpx.Client) -> None:
    if not client.is_closed:
        client.close()


async def _dispose_async_http_client(client: httpx.AsyncClient) -> None:
    if not client.is_closed:
        await client.aclose()


def _dispose_azure_credential(credential: Any) -> None:
    if hasattr(credential, "close"):
        credential.close()


@dataclass(frozen=True)
class _QueryRecordPostgres:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


async def _dispose_query_record_postgres(bundle: _QueryRecordPostgres) -> None:
    await bundle.engine.dispose()


async def _dispose_redis_client(client: Any) -> None:
    if hasattr(client, "aclose"):
        await client.aclose()
    elif hasattr(client, "close"):
        res = client.close()
        if inspect.isawaitable(res):
            await res


async def _dispose_conversation_store(store: Any) -> None:
    if hasattr(store, "_redis"):
        await _dispose_redis_client(store._redis)


def _normalize_query_record_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


@dataclass(frozen=True)
class _Slot:
    value: Any
    dispose: DisposeFn


def bind_process_resources(resources: ProcessResources | None) -> Token[ProcessResources | None]:
    """Bind the process container for ambient accessors (session_scope, HTTP)."""
    global _global_process_resources
    _global_process_resources = resources
    return _bound_resources.set(resources)


def reset_process_resources(token: Token[ProcessResources | None]) -> None:
    global _global_process_resources
    _bound_resources.reset(token)
    if _bound_resources.get() is None:
        _global_process_resources = None


def current_process_resources() -> ProcessResources:
    """Return the bound container, or raise when scripts forgot to enter one."""
    resources = _bound_resources.get()
    if resources is None:
        resources = _global_process_resources
    if resources is None:
        raise RuntimeError(
            "ProcessResources is not bound; pass resources= explicitly or "
            "enter `async with ProcessResources.from_settings()` / lifespan bind"
        )
    return resources


class ProcessResources:
    """Every process-wide resource this process creates, with one shutdown."""

    def __init__(
        self,
        *,
        record_db_url: str | None,
        query_record_db_url: str | None = None,
        adapter_statement_timeout_seconds: float = settings.adapter_statement_timeout_seconds,
        adapter_executor_max_workers: int = settings.adapter_executor_max_workers,
        create_engine_fn: Callable[..., Engine] = create_engine,
    ) -> None:
        self._record_db_url = record_db_url
        self._query_record_db_url = (
            settings.query_record_database_url.strip()
            if query_record_db_url is None
            else query_record_db_url.strip()
        )
        self._statement_timeout_seconds = adapter_statement_timeout_seconds
        self._adapter_executor_max_workers = adapter_executor_max_workers
        self._create_engine = create_engine_fn

        self._slots: dict[str, _Slot] = {}
        self._locks = {name: threading.Lock() for name in _SLOT_NAMES}
        self._shutdown_lock = threading.Lock()
        self._lifecycle = _Lifecycle.OPEN
        self._closed_event = threading.Event()
        self._bind_token: Token[ProcessResources | None] | None = None
        self._orphan_async_disposers: list[asyncio.Task[Any]] = []
        self._orphan_lock = threading.Lock()

    @classmethod
    def from_settings(cls) -> ProcessResources:
        """Container wired from process settings, for the lifespan and for eval
        and CLI entry points that own their own process."""
        return cls(
            record_db_url=settings.mcp_record_database_url,
            query_record_db_url=settings.query_record_database_url.strip() or None,
        )

    @property
    def lifecycle(self) -> str:
        return self._lifecycle.value

    def _ensure_open(self) -> None:
        if self._lifecycle is not _Lifecycle.OPEN:
            raise ProcessResourcesClosedError(
                f"ProcessResources is {self._lifecycle.value}; cannot acquire slots"
            )

    def _once(self, name: str, builder: Callable[[], Any], dispose: DisposeFn) -> Any:
        self._ensure_open()
        slot = self._slots.get(name)
        if slot is not None:
            return slot.value

        with self._locks[name]:
            self._ensure_open()
            slot = self._slots.get(name)
            if slot is not None:
                return slot.value

            value = builder()
            with self._shutdown_lock:
                if self._lifecycle is not _Lifecycle.OPEN:
                    try:
                        result = dispose(value)
                        if inspect.isawaitable(result):
                            # Built during closing — drop without publishing.
                            # Track the awaitable so aclose can join it.
                            try:
                                loop = asyncio.get_running_loop()
                            except RuntimeError:
                                asyncio.run(result)
                            else:
                                task = loop.create_task(result)
                                with self._orphan_lock:
                                    self._orphan_async_disposers.append(task)
                    except Exception as exc:
                        logger.warning("Error disposing orphan process resource %s: %s", name, exc)
                    raise ProcessResourcesClosedError(
                        f"ProcessResources is {self._lifecycle.value}; "
                        "discarded in-flight slot construction"
                    )
                self._slots[name] = _Slot(value=value, dispose=dispose)
                return value

    @property
    def record_engine(self) -> Engine:
        """MariaDB record engine with a server-side statement ceiling."""
        return self._once("record_engine", self._build_record_engine, _dispose_engine)

    def _build_record_engine(self) -> Engine:
        url = self._record_db_url
        if not url:
            raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)

        if url.startswith("sqlite"):
            return self._create_engine(url)

        connect_args = {
            "init_command": (f"SET SESSION max_statement_time = {self._statement_timeout_seconds}")
        }
        return self._create_engine(
            url,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=0,
            connect_args=connect_args,
        )

    @property
    def policy_record_engine(self) -> Engine:
        """Record-tools pool against the same database as ``record_engine``.

        A second pool, not a second URL. Every query it runs is a bounded
        read-only SELECT holding no per-request session state, so it takes
        SQLAlchemy's default sizing and no statement ceiling, where
        ``record_engine`` caps the pool at two and kills a statement at the
        configured ceiling. One owner disposes both.
        """
        return self._once("policy_record_engine", self._build_policy_record_engine, _dispose_engine)

    def _build_policy_record_engine(self) -> Engine:
        url = self._record_db_url
        if not url:
            raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)

        # The sqlite short-circuit `record_engine` carries applies here too:
        # sqlite rejects `connect_timeout`, and tests point this at memory.
        if url.startswith("sqlite"):
            return self._create_engine(url)

        return self._create_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 10},
        )

    @property
    def adapter_executor(self) -> ThreadPoolExecutor:
        """Bounded thread pool for blocking adapter jobs."""
        return self._once("adapter_executor", self._build_adapter_executor, _shutdown_executor)

    def _build_adapter_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=self._adapter_executor_max_workers)

    @property
    def document_executor(self) -> BoundedExecutor:
        """Bounded pool for document retrieval; separate from adapter jobs."""
        return self._once(
            "document_executor",
            self._build_document_executor,
            _shutdown_document_executor,
        )

    def _build_document_executor(self) -> BoundedExecutor:
        return BoundedExecutor(4, 4, thread_name_prefix="document")

    def get_document_sync_http_client(self) -> httpx.Client:
        """DocumentExecutor-owned sync client: 2s timeout, unused by Ask ingest."""
        return self._once(
            "document_sync_http_client",
            self._build_document_sync_http_client,
            _dispose_sync_http_client,
        )

    def _build_document_sync_http_client(self) -> httpx.Client:
        return httpx.Client(limits=_HTTP_LIMITS, timeout=_DOCUMENT_HTTP_TIMEOUT)

    def get_document_async_http_client(self) -> httpx.AsyncClient:
        """DocumentExecutor-owned async client: 2s timeout, unused by Ask ingest."""
        return self._once(
            "document_async_http_client",
            self._build_document_async_http_client,
            _dispose_async_http_client,
        )

    def _build_document_async_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(limits=_HTTP_LIMITS, timeout=_DOCUMENT_HTTP_TIMEOUT)

    @property
    def sync_http_client(self) -> httpx.Client:
        """Process-lifetime sync HTTP client."""
        return self.get_sync_http_client()

    def get_sync_http_client(self) -> httpx.Client:
        """Process-lifetime sync HTTP client."""
        return self._once(
            "sync_http_client", self._build_sync_http_client, _dispose_sync_http_client
        )

    def _build_sync_http_client(self) -> httpx.Client:
        return httpx.Client(limits=_HTTP_LIMITS, timeout=_HTTP_TIMEOUT)

    @property
    def async_http_client(self) -> httpx.AsyncClient:
        """Process-lifetime async HTTP client."""
        return self.get_async_http_client()

    def get_async_http_client(self) -> httpx.AsyncClient:
        """Process-lifetime async HTTP client."""
        return self._once(
            "async_http_client", self._build_async_http_client, _dispose_async_http_client
        )

    def _build_async_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(limits=_HTTP_LIMITS, timeout=_HTTP_TIMEOUT)

    @property
    def azure_credential(self) -> Any:
        """Process-lifetime DefaultAzureCredential."""
        return self.get_azure_credential()

    def get_azure_credential(self) -> Any:
        """One shared DefaultAzureCredential for every Azure client."""
        return self._once(
            "azure_credential", self._build_azure_credential, _dispose_azure_credential
        )

    def _build_azure_credential(self) -> Any:
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential()

    def _query_record_postgres(self) -> _QueryRecordPostgres:
        return self._once(
            "query_record_postgres",
            self._build_query_record_postgres,
            _dispose_query_record_postgres,
        )

    def _build_query_record_postgres(self) -> _QueryRecordPostgres:
        url = self._query_record_db_url
        if not url:
            raise RuntimeError(
                "QUERY_RECORD_DATABASE_URL is not configured — Query Record store unavailable"
            )
        engine = create_async_engine(
            _normalize_query_record_url(url),
            pool_size=5,
            max_overflow=0,
            pool_pre_ping=True,
            hide_parameters=True,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        return _QueryRecordPostgres(engine=engine, session_factory=factory)

    @property
    def query_record_engine(self) -> AsyncEngine:
        """Async Postgres engine for Query Record / evidence / ledger."""
        return self._query_record_postgres().engine

    @property
    def query_record_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Session factory for the Query Record Postgres pool."""
        return self._query_record_postgres().session_factory

    @property
    def join_redis(self) -> Any:
        """Dedicated Redis client for continuation joins with >10s timeout."""
        return self._once("join_redis", self._build_join_redis, _dispose_redis_client)

    def _build_join_redis(self) -> Any:
        import redis.asyncio as aioredis

        if not settings.redis_url:

            class _OfflineRedisClient:
                async def ping(self) -> bool:
                    return True

            return _OfflineRedisClient()

        return aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=15,
            socket_connect_timeout=2,
            health_check_interval=30,
            max_connections=20,
        )

    @property
    def conversation_store(self) -> Any:
        """Process-bound conversation store."""
        return self._once(
            "conversation_store",
            self._build_conversation_store,
            _dispose_conversation_store,
        )

    def _build_conversation_store(self) -> Any:
        import importlib

        ts = importlib.import_module("app.conversation.transcript_store")
        if not settings.conversation_enabled:
            return ts.InMemoryConversationStore()
        return ts.RedisConversationStore(settings.redis_url)

    async def aclose(self) -> None:
        """Stop acquires, dispose every built slot, then mark CLOSED.

        Idempotent. Awaited by the FastAPI lifespan so async HTTP clients
        close under the running loop (no untracked ``create_task``).
        """
        built: list[tuple[str, _Slot]] | None = None
        with self._shutdown_lock:
            if self._lifecycle is _Lifecycle.CLOSED:
                return
            if self._lifecycle is _Lifecycle.CLOSING:
                # Another closer owns disposal; wait until CLOSED.
                pass
            else:
                self._lifecycle = _Lifecycle.CLOSING
                built = list(self._slots.items())
                self._slots.clear()

        if built is None:
            await asyncio.to_thread(self._closed_event.wait)
            return

        await self._dispose_built(built)
        await self._await_orphan_async_disposers()
        with self._shutdown_lock:
            self._lifecycle = _Lifecycle.CLOSED
            self._closed_event.set()

    async def _await_orphan_async_disposers(self) -> None:
        with self._orphan_lock:
            pending = list(self._orphan_async_disposers)
            self._orphan_async_disposers.clear()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _dispose_built(self, built: list[tuple[str, _Slot]]) -> None:
        for name, slot in built:
            try:
                result = slot.dispose(slot.value)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.warning("Error disposing process resource %s: %s", name, exc)

    def shutdown(self) -> None:
        """Sync teardown for scripts and sync fixtures.

        Drives ``aclose()`` when no event loop is running. From an async
        context, callers must ``await aclose()`` instead.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.aclose())
            return
        raise RuntimeError(
            "ProcessResources.shutdown() cannot run inside a running event loop; "
            "await aclose() instead"
        )

    async def __aenter__(self) -> ProcessResources:
        self._bind_token = bind_process_resources(self)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._bind_token is not None:
            reset_process_resources(self._bind_token)
            self._bind_token = None
        await self.aclose()
