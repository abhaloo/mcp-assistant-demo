"""LCEL Runnable wrappers that emit OpenTelemetry spans."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from langchain_core.documents import Document
from langchain_core.runnables import Runnable, RunnableConfig

from app.config import settings
from app.telemetry.helpers import record_retrieval_result, set_gen_ai_usage
from app.telemetry.metrics import record_token_usage
from app.telemetry.spans import llm_span, retriever_span


class _SpanWrappedRunnable(Runnable):
    """Delegate invoke/ainvoke to an inner Runnable inside an OTel span."""

    def __init__(
        self,
        inner: Runnable,
        span_factory: Callable[[], AbstractContextManager[Any]],
        *,
        on_success: Callable[[Any, Any], None] | None = None,
    ) -> None:
        self._inner = inner
        self._span_factory = span_factory
        self._on_success = on_success

    def _finish(self, span: Any, result: Any) -> Any:
        if self._on_success is not None:
            self._on_success(span, result)
        return result

    # start_as_current_span (used by the span factories) records the exception
    # and sets ERROR status on __exit__, so these wrappers just let it propagate
    # — no explicit record_span_error needed (it would double-record).
    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        with self._span_factory() as span:
            result = self._inner.invoke(input, config, **kwargs)
            return self._finish(span, result)

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        with self._span_factory() as span:
            result = await self._inner.ainvoke(input, config, **kwargs)
            return self._finish(span, result)


def _record_doc_retrieval(span: Any, result: Any) -> None:
    if isinstance(result, list) and (not result or isinstance(result[0], Document)):
        record_retrieval_result(span, result)


def _record_llm_usage(operation: str) -> Callable[[Any, Any], None]:
    """Build an on_success hook that stamps token usage on the span and the
    gen_ai.client.token.usage metric. No-op when the provider omits usage."""

    def hook(span: Any, result: Any) -> None:
        usage = set_gen_ai_usage(span, result)
        if usage is not None:
            record_token_usage(
                operation=operation,
                model=settings.active_chat_model,
                input_tokens=usage[0],
                output_tokens=usage[1],
            )

    return hook


def instrument_retriever(
    retriever: Runnable,
    *,
    k: int | None = None,
    branch: str = "context",
    access_tiers: list[str] | None = None,
) -> Runnable:
    """Wrap a retriever Runnable with rag.retriever span attributes."""
    top_k = k if k is not None else settings.top_k
    return _SpanWrappedRunnable(
        retriever,
        span_factory=lambda: retriever_span(k=top_k, branch=branch, access_tiers=access_tiers),
        on_success=_record_doc_retrieval,
    )


def instrument_llm(llm: Runnable, *, operation: str = "generation") -> Runnable:
    """Wrap an LLM Runnable with GenAI span attributes + token usage capture."""
    return _SpanWrappedRunnable(
        llm,
        span_factory=lambda: llm_span(operation=operation),
        on_success=_record_llm_usage(operation),
    )


def wrap_with_span(inner, span_factory, *, on_success=None) -> Runnable:
    """Public composition point for span-wrapping any invoke/ainvoke object.
    The ONE delegation policy for traced wrappers (ADR 0018) — do not write
    bespoke per-chain tracer classes."""
    return _SpanWrappedRunnable(inner, span_factory, on_success=on_success)
