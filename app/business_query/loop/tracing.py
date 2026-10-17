"""The loop traces into our stores only. Any LangSmith signal refuses the loop."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.telemetry.runnable import wrap_with_span

_TRACING_ENV = ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING")


class LoopTracingRefused(RuntimeError):
    """LangSmith tracing is switched on; the loop does not run."""


def assert_loop_tracing_off(settings: Any, environ: Mapping[str, str]) -> None:
    if settings.langsmith_tracing:
        raise LoopTracingRefused("settings.langsmith_tracing must be false")
    for name in _TRACING_ENV:
        value = environ.get(name, "").strip().lower()
        if value and value != "false":
            raise LoopTracingRefused(f"{name} must be unset or 'false'")


def loop_callbacks() -> list[Any]:
    """The explicit callback list the runner passes; no tracer of any kind."""
    return []


__all__ = [
    "LoopTracingRefused",
    "assert_loop_tracing_off",
    "loop_callbacks",
    "wrap_with_span",
]
