"""Explicit OTel context propagation for thread-pool execution."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from opentelemetry import trace

T = TypeVar("T")


def current_query_id() -> str:
    """Correlation id for guardrails audit rows: the active OTel trace id,
    or a uuid4 when called outside a request (CLI, scripts). Leaf modules
    must never mint identity themselves (ADR 0020)."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return str(uuid.uuid4())


async def run_in_thread(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """
    Run a sync callable in a worker thread with trace context preserved.

    ``asyncio.to_thread`` (Python 3.11+) copies contextvars into the worker,
    which is how OpenTelemetry stores the active span. A characterization test
    in ``tests/telemetry/test_telemetry.py`` pins this nesting guarantee.
    """
    return await asyncio.to_thread(func, *args, **kwargs)
