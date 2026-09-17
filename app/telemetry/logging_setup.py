"""Log ↔ trace correlation — inject trace/span IDs into every LogRecord."""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace

_logging_configured = False

LOG_FORMAT = (
    "%(asctime)s %(levelname)s "
    "[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] "
    "%(name)s: %(message)s"
)


def _inject_trace_context(record: logging.LogRecord) -> logging.LogRecord:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        record.otelTraceID = format(ctx.trace_id, "032x")
        record.otelSpanID = format(ctx.span_id, "016x")
        record.otelTraceSampled = ctx.trace_flags.sampled
    else:
        record.otelTraceID = "0" * 32
        record.otelSpanID = "0" * 16
        record.otelTraceSampled = False
    return record


def configure_logging() -> None:
    """Wire stdlib logging so each line carries the active trace context (Workstream E)."""
    global _logging_configured
    if _logging_configured:
        return

    old_factory = logging.getLogRecordFactory()

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        return _inject_trace_context(old_factory(*args, **kwargs))

    logging.setLogRecordFactory(record_factory)

    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    else:
        for handler in root.handlers:
            handler.setFormatter(logging.Formatter(LOG_FORMAT))

    _logging_configured = True
