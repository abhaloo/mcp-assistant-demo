"""Application correlation ID stamping on OpenTelemetry spans."""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_thread_id: ContextVar[str | None] = ContextVar("thread_id", default=None)


def normalize_run_id(run_id: str) -> str:
    """Normalize a run_id into a 32-character hex correlation ID."""
    if len(run_id) == 32 and all(c in "0123456789abcdef" for c in run_id):
        return run_id
    return hashlib.sha256(run_id.encode()).hexdigest()[:32]


def bind_correlation_id(run_id: str) -> None:
    """Bind ``run_id`` to the request context and stamp the active span."""
    _correlation_id.set(run_id)
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("correlation_id", run_id)


def clear_correlation_id() -> None:
    _correlation_id.set(None)


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def bind_thread_id(thread_id: str | None) -> None:
    """Bind the client conversation thread id for this request context."""
    _thread_id.set(thread_id or None)


def clear_thread_id() -> None:
    _thread_id.set(None)


def current_thread_id() -> str | None:
    return _thread_id.get()


class CorrelationSpanProcessor(SpanProcessor):
    """Copy ``correlation_id`` from parent context onto every started child span."""

    def on_start(self, span: Span, parent_context: Any | None = None) -> None:
        correlation_id = _correlation_id.get()
        if correlation_id and span.is_recording():
            span.set_attribute("correlation_id", correlation_id)

    def on_end(self, span: ReadableSpan) -> None:
        return

    def shutdown(self) -> None:
        return

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
