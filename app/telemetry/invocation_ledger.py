"""Full-fidelity invocation + SQL ledger (ADR 0053 D-U6).

Owned Postgres tables capture complete prompts, responses, reasoning, and SQL
with literal values. Masked surfaces (logs, eval trace artifacts, LangSmith)
stay unchanged — this is a separate store beside ``QueryTrace``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from sqlalchemy import BigInteger, DateTime, Index, Numeric, String, Text, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings
from app.crypto.event_keyring import EventEncryptionKeyring
from app.db.postgres import session_scope
from app.pricing.pricer import price_tokens
from app.providers.model_purpose import ModelPurpose
from app.telemetry.correlation import current_correlation_id
from app.telemetry.helpers import token_usage_from_message
from app.telemetry.invocation_payload import (
    ExecutionIdentity,
    InvocationExpectation,
    InvocationPayload,
    TerminalEvidenceError,
    TerminalEvidenceReceipt,
    clear_recorded_evidence_for_tests,
    encrypt_invocation_payload,
    extract_bounded_response_metadata,
    get_recorded_evidence,
    record_evidence_invocation,
    require_terminal_evidence,
)

_logger = logging.getLogger(__name__)

_ledger_scope: ContextVar[str] = ContextVar("ledger_scope", default="prod")
_dsn_absent_warned = False
_pending_tasks: set[asyncio.Task[None] | asyncio.Future[None]] = set()

# Cross-thread scheduling (revert-sensitive -- see `_schedule` docstring).
# Ledger writes are triggered from BOTH the app's event-loop thread (async
# call sites: the factory-attached model observer on a direct `.invoke()`,
# SSE/JSON dispatch) and worker threads with NO running loop of their own
# (the Business Query adapter's `loop.run_in_executor` calls in
# app/business_query/module.py, and `asyncio.to_thread` renderer calls in
# business_query_service.py). `asyncio.create_task()` requires a loop
# running IN THE CALLING THREAD and raises `RuntimeError` otherwise --
# module.py's broad `except Exception` around the adapter call then turns
# an already-successful query into `Incomplete`. `register_event_loop()` is
# called once from a guaranteed-loop-thread startup hook (FastAPI lifespan,
# the eval script's async entrypoint) so `_schedule` can fall back to
# `run_coroutine_threadsafe` when called off-loop.
_loop_lock = threading.Lock()
_registered_loop: asyncio.AbstractEventLoop | None = None


def register_event_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Register the app's event loop for cross-thread ledger scheduling.

    Call once from a coroutine running on the loop that will serve the
    app's whole lifetime (FastAPI lifespan startup, or an eval script's
    top-level `asyncio.run` entrypoint). Safe to call repeatedly -- each
    call from the loop thread also self-registers via `_schedule`, so this
    is a belt-and-braces guarantee for the FIRST ledger write, which may
    otherwise land on a worker thread before any loop-thread write has run.
    """
    resolved = loop if loop is not None else asyncio.get_running_loop()
    with _loop_lock:
        global _registered_loop
        _registered_loop = resolved


def _log_threadsafe_future_exception(future: asyncio.Future[None]) -> None:
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        # type(exc).__name__ ONLY -- never exc_info/str(exc). A SQLAlchemy
        # StatementError's own __str__ appends "[SQL: ...] [parameters: ...]",
        # which for THIS table would put full-fidelity content (ADR 0053 D-U6)
        # into the general application log -- exactly the masked surface that
        # decision says must stay untouched.
        _logger.warning("invocation ledger write failed (fail-open): %s", type(exc).__name__)


def _schedule(coro: Coroutine[Any, Any, None]) -> None:
    """Schedule *coro*, regardless of which thread calls this from."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        with _loop_lock:
            global _registered_loop
            _registered_loop = loop
        task = loop.create_task(coro)
        _pending_tasks.add(task)
        task.add_done_callback(_pending_tasks.discard)
        return

    with _loop_lock:
        target_loop = _registered_loop
    if target_loop is None or target_loop.is_closed():
        coro.close()  # avoid a "coroutine was never awaited" warning
        _logger.warning("invocation ledger: no event loop registered, write dropped")
        return
    concurrent_future = asyncio.run_coroutine_threadsafe(coro, target_loop)
    try:
        wrapped = asyncio.wrap_future(concurrent_future, loop=target_loop)
    except RuntimeError:
        wrapped = None
    if wrapped is not None:
        _pending_tasks.add(wrapped)
        wrapped.add_done_callback(lambda f: _pending_tasks.discard(f))
    concurrent_future.add_done_callback(_log_threadsafe_future_exception)


class _LedgerBase(DeclarativeBase):
    pass


class ModelInvocationRow(_LedgerBase):
    __tablename__ = "model_invocations"
    __table_args__ = (
        Index("ix_model_invocations_scope_created", "scope", "created_at"),
        Index("ix_model_invocations_correlation", "correlation_id"),
        Index("ix_model_invocations_payload_digest", "payload_digest"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    route_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_messages: Mapped[str] = mapped_column(Text, nullable=False)
    response_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ttft_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    estimated_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    cost_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pii_posture: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SqlExecutionRow(_LedgerBase):
    __tablename__ = "sql_executions"
    __table_args__ = (
        Index("ix_sql_executions_scope_created", "scope", "created_at"),
        Index("ix_sql_executions_correlation", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    scope: Mapped[str] = mapped_column(String(8), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    adapter: Mapped[str] = mapped_column(String(32), nullable=False, default="internal_compiler")
    receipt_query_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


def ledger_store_configured() -> bool:
    return bool(settings.query_record_database_url.strip())


def set_ledger_scope(scope: str) -> None:
    _ledger_scope.set(scope)


def get_ledger_scope() -> str:
    return _ledger_scope.get()


def _warn_dsn_absent_once() -> None:
    global _dsn_absent_warned
    if _dsn_absent_warned:
        return
    _dsn_absent_warned = True
    _logger.warning("invocation ledger DSN absent — model/SQL ledger writes are no-ops")


def reset_ledger_warnings_for_tests() -> None:
    global _dsn_absent_warned
    _dsn_absent_warned = False


def _serialize_messages(messages: list[list[BaseMessage]] | list[BaseMessage]) -> str:
    if not messages:
        return "[]"
    first = messages[0]
    if isinstance(first, list):
        batch = first
    else:
        batch = messages  # type: ignore[assignment]
    payload = []
    for msg in batch:
        payload.append(
            {
                "type": msg.type,
                "content": msg.content,
            }
        )
    return json.dumps(payload, default=str, ensure_ascii=False)


def extract_token_usage(response: LLMResult) -> tuple[int | None, int | None, int | None]:
    """Extract (input_tokens, output_tokens, reasoning_tokens) from an LLMResult.

    Precedence:
    1. Message ``usage_metadata`` (modern LangChain normalized shape).
    2. LLMResult-level ``llm_output["token_usage"]`` / ``["usage"]`` (legacy provider shape).
    3. Message ``response_metadata["token_usage"]`` / ``["usage"]`` (legacy message shape).
    """
    message = None
    if response.generations and response.generations[0]:
        gen = response.generations[0][0]
        if isinstance(gen, ChatGeneration):
            message = gen.message

    if message is not None and getattr(message, "usage_metadata", None):
        return token_usage_from_message(message)

    llm_output = response.llm_output or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage")
    if usage:
        input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
        output_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
        reasoning_tokens = usage.get("reasoning_tokens")
        return (
            int(input_tokens) if input_tokens is not None else None,
            int(output_tokens) if output_tokens is not None else None,
            int(reasoning_tokens) if reasoning_tokens is not None else None,
        )

    if message is not None:
        return token_usage_from_message(message)

    return None, None, None


def _extract_response_text(response: LLMResult) -> tuple[str | None, str | None]:
    if not response.generations:
        return None, None
    gen = response.generations[0][0]
    if not isinstance(gen, ChatGeneration) or gen.message is None:
        text = getattr(gen, "text", None)
        return (str(text) if text is not None else None, None)
    message = gen.message
    content = message.content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
        text = "".join(text_parts) if text_parts else None
    else:
        text = str(content) if content is not None else None
    tool_calls = getattr(message, "tool_calls", None)
    if (not text or not text.strip()) and tool_calls:
        text = json.dumps(tool_calls, default=str)
    meta = getattr(message, "response_metadata", None) or {}
    reasoning = meta.get("reasoning_evidence") or meta.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        reasoning = json.dumps(reasoning, default=str)
    return text, reasoning if isinstance(reasoning, str) else None


@dataclass(frozen=True)
class ModelInvocationRecord:
    scope: str
    correlation_id: str | None
    purpose: str
    route_key: str | None
    model: str | None
    request_messages: str
    response_content: str | None
    reasoning_content: str | None
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    latency_ms: int | None
    estimated_usd: Decimal | None
    cost_status: str | None
    ttft_ms: int | None = None
    error_class: str | None = None
    pii_posture: str | None = None
    provider: str | None = None
    encrypted_payload: str | None = None
    payload_digest: str | None = None
    key_version: str | None = None
    nonce: str | None = None


@dataclass(frozen=True)
class SqlExecutionRecord:
    scope: str
    correlation_id: str | None
    statement: str
    params_json: str | None
    elapsed_ms: int | None
    row_count: int | None
    adapter: str = "internal_compiler"
    receipt_query_id: str | None = None


@dataclass(frozen=True)
class SqlExecutionTiming:
    """Eval-safe SQL ledger slice: ids and elapsed_ms only, never statement text."""

    id: int
    elapsed_ms: int | None
    correlation_id: str | None


class InvocationLedger:
    """Async fire-and-forget writer for ledger rows.

    Never propagates a write failure -- these tasks are unawaited by the
    caller (scheduled via ``_schedule``), so a transient Postgres error here
    can never break the request that triggered it either way. The
    try/except makes that failure OBSERVABLE (a counted, logged warning,
    mirroring the sibling ``query_records`` writer's fail-open pattern in
    ``app/query_records/dispatch.py::_persist_snapshot``) instead of leaving
    it to asyncio's generic "Task exception was never retrieved" logging.

    Logs ``type(exc).__name__`` ONLY -- never ``exc_info``/``str(exc)``. A
    SQLAlchemy ``StatementError``'s own ``__str__`` appends
    ``[SQL: ...] [parameters: ...]``, which for these tables would put
    full-fidelity content (ADR 0053 D-U6) into the general application log --
    the exact masked surface that decision says must stay untouched. Mirrors
    the existing ``internal_adapter.py`` convention of logging
    ``type(exc).__name__``/``exc.orig`` rather than ``str(exc)``.

    ``write_model_invocation``/``write_sql_execution`` are near-identical
    modulo one insert helper and one table name -- kept as two named methods
    (not one parameterized ``_write``) because tests monkeypatch this class
    at the attribute level (``monkeypatch.setattr(InvocationLedger,
    "write_sql_execution", ...)`` -- see ``tests/business_query/test_module.py``).
    A single parameterized method would still support that pattern, but the
    duplication is deliberate here, not accidental.
    """

    async def write_model_invocation(self, record: ModelInvocationRecord) -> None:
        if not ledger_store_configured():
            _warn_dsn_absent_once()
            return
        try:
            async with session_scope() as session:
                await _insert_model_invocation(session, record)
                await session.commit()
        except Exception as exc:
            _logger.warning(
                "model_invocations ledger write failed (fail-open): %s", type(exc).__name__
            )

    async def write_sql_execution(self, record: SqlExecutionRecord) -> None:
        if not ledger_store_configured():
            _warn_dsn_absent_once()
            return
        try:
            async with session_scope() as session:
                await _insert_sql_execution(session, record)
                await session.commit()
        except Exception as exc:
            _logger.warning(
                "sql_executions ledger write failed (fail-open): %s", type(exc).__name__
            )


async def _insert_model_invocation(session: AsyncSession, record: ModelInvocationRecord) -> None:
    stmt = pg_insert(ModelInvocationRow).values(
        created_at=datetime.now(UTC),
        **asdict(record),
    )
    await session.execute(stmt)


async def _insert_sql_execution(session: AsyncSession, record: SqlExecutionRecord) -> None:
    stmt = pg_insert(SqlExecutionRow).values(
        created_at=datetime.now(UTC),
        **asdict(record),
    )
    await session.execute(stmt)


async def _execute_in_current_scope(build_stmt: Callable[[str], Any]) -> Any:
    scope = get_ledger_scope()
    async with session_scope() as session:
        return await session.execute(build_stmt(scope))


async def query_durable_invocation_evidence_for_correlations(
    correlation_ids: tuple[str, ...],
) -> list[ModelInvocationRow]:
    """Read durable ``model_invocations`` rows for correlation IDs in the current scope."""
    if not correlation_ids:
        return []
    result = await _execute_in_current_scope(
        lambda scope: select(ModelInvocationRow).where(
            ModelInvocationRow.correlation_id.in_(correlation_ids),
            ModelInvocationRow.scope == scope,
        )
    )
    return list(result.scalars().all())


async def query_durable_invocation_evidence(correlation_id: str) -> list[ModelInvocationRow]:
    """Read durable ``model_invocations`` rows for one correlation ID.

    ``require_terminal_evidence`` treats this as the source of truth once the
    ledger store is configured. The in-memory evidence map stays a fast-path
    cache and never overrides what this returns. Rows are limited to
    ``get_ledger_scope()``.
    """
    return await query_durable_invocation_evidence_for_correlations((correlation_id,))


async def query_durable_sql_timings_for_correlations(
    correlation_ids: tuple[str, ...],
) -> list[SqlExecutionTiming]:
    """Read SQL ids and elapsed_ms for correlation IDs in the current scope.

    Statement text and bind parameters stay on the ORM row and are not selected.
    """
    if not correlation_ids:
        return []
    result = await _execute_in_current_scope(
        lambda scope: select(
            SqlExecutionRow.id,
            SqlExecutionRow.elapsed_ms,
            SqlExecutionRow.correlation_id,
        ).where(
            SqlExecutionRow.correlation_id.in_(correlation_ids),
            SqlExecutionRow.scope == scope,
        )
    )
    return [
        SqlExecutionTiming(
            id=row.id,
            elapsed_ms=row.elapsed_ms,
            correlation_id=row.correlation_id,
        )
        for row in result.all()
    ]


async def query_durable_sql_executions(correlation_id: str) -> list[SqlExecutionRow]:
    """Read durable ``sql_executions`` rows for one correlation ID in the current scope."""
    result = await _execute_in_current_scope(
        lambda scope: select(SqlExecutionRow).where(
            SqlExecutionRow.correlation_id == correlation_id,
            SqlExecutionRow.scope == scope,
        )
    )
    return list(result.scalars().all())


_ledger = InvocationLedger()


def schedule_model_invocation(record: ModelInvocationRecord) -> None:
    if not ledger_store_configured():
        _warn_dsn_absent_once()
        return
    _schedule(_ledger.write_model_invocation(record))


def schedule_sql_execution(record: SqlExecutionRecord) -> None:
    if not ledger_store_configured():
        _warn_dsn_absent_once()
        return
    _schedule(_ledger.write_sql_execution(record))


async def flush_ledger_writes() -> None:
    if _pending_tasks:
        await asyncio.gather(*list(_pending_tasks), return_exceptions=True)


def _chunk_has_content(content: Any) -> bool:
    if not content:
        return False
    if isinstance(content, str):
        return content != ""
    if isinstance(content, list):
        return len(content) > 0
    return True


def _chunk_has_reasoning(msg: Any, content: Any) -> bool:
    additional = getattr(msg, "additional_kwargs", None) or {}
    resp_meta = getattr(msg, "response_metadata", None) or {}
    keys = ("reasoning", "reasoning_content", "reasoning_details", "reasoning_text")
    if any(additional.get(k) or resp_meta.get(k) for k in keys) or resp_meta.get(
        "reasoning_evidence"
    ):
        return True
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") in ("reasoning", "thought") for b in content
        )
    return False


def _is_qualifying_stream_chunk(token: str, chunk: Any) -> bool:
    if token:
        return True
    if chunk is None:
        return False
    msg = getattr(chunk, "message", chunk)
    content = getattr(msg, "content", None)
    return bool(
        getattr(chunk, "text", None)
        or _chunk_has_content(content)
        or getattr(msg, "tool_calls", None)
        or getattr(msg, "tool_call_chunks", None)
        or _chunk_has_reasoning(msg, content)
    )


class InvocationLedgerCallbackHandler(BaseCallbackHandler):
    """Factory-attached observer for every ``get_chat_model`` result."""

    run_inline: bool = True

    def __init__(
        self,
        *,
        scope: str,
        purpose: ModelPurpose,
        route_key: str | None,
        model: str | None,
        pii_posture: str | None = None,
        provider: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__()
        self._scope = scope
        self._purpose = purpose
        self._route_key = route_key
        self._provider = provider
        self._model = model
        self._pii_posture = pii_posture
        self._correlation_id = correlation_id
        self._started_at: dict[UUID, float] = {}
        self._request_messages: dict[UUID, str] = {}
        self._ttft_ms: dict[UUID, int] = {}

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._started_at.setdefault(run_id, time.perf_counter())
        if run_id not in self._request_messages:
            self._request_messages[run_id] = json.dumps(prompts, default=str, ensure_ascii=False)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._started_at[run_id] = time.perf_counter()
        self._request_messages[run_id] = _serialize_messages(messages)

    def on_llm_new_token(
        self,
        token: str,
        *,
        chunk: Any = None,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        if run_id in self._ttft_ms:
            return
        if not _is_qualifying_stream_chunk(token, chunk):
            return
        started = self._started_at.get(run_id)
        if started is not None:
            self._ttft_ms[run_id] = max(0, int((time.perf_counter() - started) * 1000))

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._started_at.pop(run_id, None)
        ttft_ms = self._ttft_ms.pop(run_id, None)
        request_messages = self._request_messages.pop(run_id, "[]")
        latency_ms = None
        if started is not None:
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        input_tokens, output_tokens, reasoning_tokens = extract_token_usage(response)
        response_content, reasoning_content = _extract_response_text(response)
        priced = price_tokens(
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        effective_cid = self._correlation_id or current_correlation_id()

        encrypted_payload = None
        payload_digest = None
        key_version = None
        nonce = None
        if settings.business_query_event_encryption_keys:
            try:
                keyring = EventEncryptionKeyring.parse(
                    settings.business_query_event_encryption_keys
                )
                bounded_meta = extract_bounded_response_metadata(response)
                payload_obj = InvocationPayload(
                    request_messages=request_messages,
                    response_content=response_content,
                    reasoning_content=reasoning_content,
                    raw_response_metadata=bounded_meta,
                )
                aad = f"correlation_id:{effective_cid}".encode() if effective_cid else None
                encrypted = encrypt_invocation_payload(keyring, payload_obj, aad=aad)
                encrypted_payload = encrypted.to_json()
                payload_digest = encrypted.digest
                key_version = encrypted.key_version
                nonce = encrypted.nonce
            except Exception as exc:
                _logger.warning(
                    "failed to encrypt model invocation payload (fail-open): %s",
                    type(exc).__name__,
                )

        record = ModelInvocationRecord(
            scope=self._scope,
            correlation_id=effective_cid,
            purpose=self._purpose.value,
            route_key=self._route_key,
            provider=self._provider,
            model=self._model,
            request_messages=request_messages,
            response_content=response_content,
            reasoning_content=reasoning_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            estimated_usd=priced.estimated_usd,
            cost_status=priced.cost_status,
            pii_posture=self._pii_posture,
            encrypted_payload=encrypted_payload,
            payload_digest=payload_digest,
            key_version=key_version,
            nonce=nonce,
        )
        schedule_model_invocation(record)
        if effective_cid and not ledger_store_configured():
            record_evidence_invocation(effective_cid, record)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        started = self._started_at.pop(run_id, None)
        self._ttft_ms.pop(run_id, None)
        request_messages = self._request_messages.pop(run_id, "[]")
        latency_ms = None
        if started is not None:
            latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        effective_cid = self._correlation_id or current_correlation_id()
        record = ModelInvocationRecord(
            scope=self._scope,
            correlation_id=effective_cid,
            purpose=self._purpose.value,
            route_key=self._route_key,
            provider=self._provider,
            model=self._model,
            request_messages=request_messages,
            response_content=None,
            reasoning_content=None,
            input_tokens=None,
            output_tokens=None,
            reasoning_tokens=None,
            latency_ms=latency_ms,
            estimated_usd=None,
            cost_status="unknown",
            error_class=type(error).__name__,
            pii_posture=self._pii_posture,
        )
        schedule_model_invocation(record)
        if effective_cid and not ledger_store_configured():
            record_evidence_invocation(effective_cid, record)


def ledger_observer(
    *,
    scope: str | None = None,
    purpose: ModelPurpose,
    route_key: str | None,
    model: str | None,
    pii_posture: str | None = None,
    provider: str | None = None,
    correlation_id: str | None = None,
) -> InvocationLedgerCallbackHandler:
    return InvocationLedgerCallbackHandler(
        scope=scope or get_ledger_scope(),
        purpose=purpose,
        route_key=route_key,
        model=model,
        pii_posture=pii_posture,
        provider=provider,
        correlation_id=correlation_id,
    )


def attach_ledger_observer(
    model: Any,
    *,
    purpose: ModelPurpose,
    route_key: str | None,
    model_name: str | None,
    pii_posture: str | None = None,
    provider: str | None = None,
    correlation_id: str | None = None,
) -> Any:
    """Attach the ledger callback to a chat model (no-op when DSN absent).

    Attaches to the chat model's OWN ``callbacks`` rather than wrapping it with
    ``with_config``. A wrapper is dropped the moment a caller derives from the
    model -- ``with_structured_output`` proxies to the inner model and returns a
    fresh object -- which silently hid every planner invocation from the ledger.
    Registering on the model itself means each derived runnable still carries
    the handler, and ``get_chat_model`` keeps returning the model type its
    callers expect.
    """
    if not ledger_store_configured():
        _warn_dsn_absent_once()
        return model
    handler = ledger_observer(
        purpose=purpose,
        route_key=route_key,
        model=model_name,
        pii_posture=pii_posture,
        provider=provider,
        correlation_id=correlation_id,
    )
    # CapabilityChatModel is a proxy; the callbacks live on the chat model it
    # wraps, which is what every derived runnable is built from.
    target = getattr(model, "inner", model)
    existing = getattr(target, "callbacks", None)
    if isinstance(existing, list):
        existing.append(handler)
        return model
    if existing is None and hasattr(target, "callbacks"):
        target.callbacks = [handler]
        return model
    # A handler manager or an unknown shape -- fall back to a wrapper rather
    # than mutating something whose contract we do not own.
    return model.with_config({"callbacks": [handler]})


def record_sql_execution(
    *,
    statement: str,
    elapsed_ms: float,
    row_count: int | None,
    receipt_query_id: str | None = None,
    params_json: str | None = None,
    scope: str | None = None,
    correlation_id: str | None = None,
) -> None:
    """``scope``/``correlation_id`` accept an explicit override for callers
    that run off the event-loop thread (e.g. the Business Query adapter's
    ``ThreadPoolExecutor`` worker) -- ``ContextVar.get()`` does NOT
    propagate into that thread (unlike ``asyncio.to_thread``), so
    ``get_ledger_scope()``/``current_correlation_id()`` would silently
    return their defaults there. Loop-thread callers can omit both and keep
    reading the ContextVars as before."""
    schedule_sql_execution(
        SqlExecutionRecord(
            scope=scope or get_ledger_scope(),
            correlation_id=correlation_id
            if correlation_id is not None
            else current_correlation_id(),
            statement=statement,
            params_json=params_json,
            elapsed_ms=max(0, int(round(elapsed_ms))),
            row_count=row_count,
            receipt_query_id=receipt_query_id,
        )
    )


__all__ = [
    "ExecutionIdentity",
    "InvocationExpectation",
    "InvocationLedger",
    "InvocationLedgerCallbackHandler",
    "ModelInvocationRecord",
    "ModelInvocationRow",
    "SqlExecutionRecord",
    "SqlExecutionRow",
    "SqlExecutionTiming",
    "TerminalEvidenceError",
    "TerminalEvidenceReceipt",
    "attach_ledger_observer",
    "clear_recorded_evidence_for_tests",
    "extract_token_usage",
    "flush_ledger_writes",
    "get_ledger_scope",
    "get_recorded_evidence",
    "ledger_observer",
    "ledger_store_configured",
    "query_durable_invocation_evidence",
    "query_durable_invocation_evidence_for_correlations",
    "query_durable_sql_executions",
    "query_durable_sql_timings_for_correlations",
    "record_evidence_invocation",
    "record_sql_execution",
    "register_event_loop",
    "require_terminal_evidence",
    "reset_ledger_warnings_for_tests",
    "schedule_model_invocation",
    "schedule_sql_execution",
    "set_ledger_scope",
]
