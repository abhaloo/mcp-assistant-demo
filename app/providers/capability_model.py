"""Capability-aware chat model wrapper — fail-closed bind_tools."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableConfig

from app.providers.azure_reasoning import (
    coerce_azure_reasoning_invoke_kwargs,
    needs_azure_reasoning_completion_args,
)
from app.providers.model_registry import CapabilityError, ModelSpec
from app.providers.reasoning import (
    extract_reasoning_evidence,
    normalize_answer_content,
    preserve_reasoning_for_replay,
)


class CapabilityChatModel(Runnable):
    """Wraps a BaseChatModel with registry-driven capability gates.

    Subclasses ``Runnable`` so LCEL ``prompt | model`` chains compose without
    TypeError.
    """

    def __init__(self, inner: BaseChatModel, spec: ModelSpec) -> None:
        self._inner = inner
        self._spec = spec

    @property
    def inner(self) -> BaseChatModel:
        return self._inner

    @property
    def spec(self) -> ModelSpec:
        return self._spec

    def bind_tools(self, tools: Any, **kwargs: Any) -> CapabilityChatModel:
        if not self._spec.supports_tools:
            raise CapabilityError(self._spec.name, "tools")
        bound = self._inner.bind_tools(tools, **kwargs)
        return CapabilityChatModel(inner=bound, spec=self._spec)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> CapabilityChatModel:
        if self._spec.structured_output_mode == "none":
            raise CapabilityError(self._spec.name, "structured_output")
        method = kwargs.get("method")
        expected_method = {
            "json_schema": "json_schema",
            "json_object": "json_mode",
        }[self._spec.structured_output_mode]
        if method != expected_method:
            raise CapabilityError(self._spec.name, "structured_output")
        structured = self._inner.with_structured_output(schema, **kwargs)
        return CapabilityChatModel(inner=structured, spec=self._spec)

    def _coerce_invoke_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self._spec.client_kind != "azure":
            return kwargs
        if not needs_azure_reasoning_completion_args(self._spec.name):
            return kwargs
        return coerce_azure_reasoning_invoke_kwargs(kwargs)

    def _maybe_normalize(self, result: Any) -> Any:
        if not self._spec.returns_reasoning or not isinstance(result, BaseMessage):
            return result
        if preserve_reasoning_for_replay(result):
            return result
        evidence = extract_reasoning_evidence(result)
        normalized = normalize_answer_content(result)
        if not evidence:
            return normalized
        # Stash for eval telemetry; content/kwargs stay stripped for judges/prod.
        metadata = dict(getattr(normalized, "response_metadata", None) or {})
        metadata["reasoning_evidence"] = evidence
        return normalized.model_copy(update={"response_metadata": metadata})

    def invoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        kwargs = self._coerce_invoke_kwargs(kwargs)
        return self._maybe_normalize(self._inner.invoke(input, config=config, **kwargs))

    def stream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        kwargs = self._coerce_invoke_kwargs(kwargs)
        for chunk in self._inner.stream(input, config=config, **kwargs):
            yield self._maybe_normalize(chunk)

    async def ainvoke(self, input: Any, config: RunnableConfig | None = None, **kwargs: Any) -> Any:
        kwargs = self._coerce_invoke_kwargs(kwargs)
        result = await self._inner.ainvoke(input, config=config, **kwargs)
        return self._maybe_normalize(result)

    async def astream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        kwargs = self._coerce_invoke_kwargs(kwargs)
        async for chunk in self._inner.astream(input, config=config, **kwargs):
            yield self._maybe_normalize(chunk)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
