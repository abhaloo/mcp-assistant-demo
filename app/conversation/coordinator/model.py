"""Coordinator model protocol and ProviderCoordinatorModel adapter."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from app.conversation.coordinator.context_budget import (
    ContextBudgetExceeded,
    ContextTooLarge,
    build_budgeted_input,
)
from app.conversation.coordinator.contracts import (
    Clarify,
    CoordinatorAction,
    CoordinatorContext,
    ExplainSources,
    FinishAnswer,
    Observation,
    QueryBusiness,
    SearchDocuments,
)
from app.conversation.coordinator.prompt import CoordinatorPrompt
from app.core.turn_budget import TurnBudget, await_with_budget
from app.providers.capability_model import CapabilityChatModel
from app.providers.context_budget import ProviderContextCounter
from app.providers.model_registry import CapabilityError

MalformedKind = Literal["tool_call_count", "unknown_tool", "invalid_arguments"]


class MalformedDecisionError(Exception):
    """Raised when the model response contains zero, multiple, unknown, or invalid tool calls.

    ``kind`` and ``tool`` are a closed vocabulary the caller may log; the
    message can carry the model's argument text and must not be logged.
    """

    redacted_text: str = "[redacted]"

    def __init__(self, message: str, *, kind: MalformedKind, tool: str | None = None) -> None:
        super().__init__(message)
        self.kind: MalformedKind = kind
        self.tool = tool


ACTION_SCHEMAS = (
    QueryBusiness,
    SearchDocuments,
    ExplainSources,
    FinishAnswer,
    Clarify,
)

_ACTION_MODELS: dict[str, type[BaseModel]] = {
    "query_business": QueryBusiness,
    "QueryBusiness": QueryBusiness,
    "search_documents": SearchDocuments,
    "SearchDocuments": SearchDocuments,
    "explain_sources": ExplainSources,
    "ExplainSources": ExplainSources,
    "finish_answer": FinishAnswer,
    "FinishAnswer": FinishAnswer,
    "clarify": Clarify,
    "Clarify": Clarify,
}

_KIND_MAP: dict[str, str] = {
    "query_business": "query_business",
    "QueryBusiness": "query_business",
    "search_documents": "search_documents",
    "SearchDocuments": "search_documents",
    "explain_sources": "explain_sources",
    "ExplainSources": "explain_sources",
    "finish_answer": "finish_answer",
    "FinishAnswer": "finish_answer",
    "clarify": "clarify",
    "Clarify": "clarify",
}


@runtime_checkable
class CoordinatorModel(Protocol):
    """Governed provider protocol for coordinator decisions."""

    async def decide(
        self,
        context: CoordinatorContext,
        observations: tuple[Observation, ...],
        *,
        allowed_actions: frozenset[str],
        budget: TurnBudget,
    ) -> CoordinatorAction: ...


class ProviderCoordinatorModel:
    """Production implementation over CapabilityChatModel.

    Binds the five action schemas with parallel_tool_calls=False and a required
    tool choice, makes exactly one
    provider call per decision inside await_with_budget, and normalises the single
    native tool call into CoordinatorAction. Zero or several calls raise
    MalformedDecisionError; no repair loop.
    """

    def __init__(
        self,
        chat_model: CapabilityChatModel,
        *,
        prompt: CoordinatorPrompt,
        new_callbacks: Callable[[], list[Any]] = list,
    ) -> None:
        if not chat_model.spec.supports_tools:
            raise CapabilityError(chat_model.spec.name, "tools")
        if chat_model.spec.context_profile is None:
            raise CapabilityError(chat_model.spec.name, "context_profile")
        self._chat_model = chat_model
        self._prompt = prompt
        self._new_callbacks = new_callbacks
        # Exactly one decision per call, and a decision is always a tool call:
        # a plain-text reply would end the turn as malformed.
        self._bound_model = self._bind(ACTION_SCHEMAS)

    def _bind(self, schemas: Sequence[type[BaseModel]]) -> CapabilityChatModel:
        return self._chat_model.bind_tools(
            list(schemas), parallel_tool_calls=False, tool_choice="required"
        )

    async def decide(
        self,
        context: CoordinatorContext,
        observations: tuple[Observation, ...],
        *,
        allowed_actions: frozenset[str],
        budget: TurnBudget,
    ) -> CoordinatorAction:
        assert self._chat_model.spec.context_profile is not None
        counter = ProviderContextCounter(self._chat_model, self._chat_model.spec.context_profile)
        budgeted = build_budgeted_input(
            context,
            observations,
            prompt=self._prompt,
            allowed_actions=allowed_actions,
            counter=counter,
        )
        if isinstance(budgeted, ContextTooLarge):
            raise ContextBudgetExceeded(
                tokens=budgeted.tokens,
                bytes=budgeted.bytes,
                mandatory_only=budgeted.mandatory_only,
            )

        # Offer only the allowed actions: a limit the graph imposes must not
        # become a choice the model can still make.
        offered = [s for s in ACTION_SCHEMAS if _KIND_MAP[s.__name__] in allowed_actions]
        bound = self._bind(offered) if offered else self._bound_model

        callbacks = self._new_callbacks()
        config: dict[str, Any] = {"callbacks": callbacks}

        async def _stream() -> Any:
            accumulated: Any = None
            async for chunk in bound.astream(budgeted.messages, config=config):
                accumulated = chunk if accumulated is None else accumulated + chunk
            return accumulated

        response = await await_with_budget(_stream, budget)

        tool_calls: list[dict[str, Any]] = getattr(response, "tool_calls", None) or []
        if len(tool_calls) != 1:
            raise MalformedDecisionError(
                f"Expected exactly 1 tool call, got {len(tool_calls)} "
                "(zero or multiple calls are rejected)",
                kind="tool_call_count",
            )

        call = tool_calls[0]
        tool_name = call.get("name") or ""
        model_cls = _ACTION_MODELS.get(tool_name)
        if model_cls is None:
            raise MalformedDecisionError(
                f"unknown tool name from model: {tool_name!r}", kind="unknown_tool", tool=tool_name
            )

        args = dict(call.get("args") or {})
        args.setdefault("kind", _KIND_MAP[tool_name])

        try:
            action = model_cls.model_validate(args)
        except ValidationError as exc:
            raise MalformedDecisionError(
                f"Failed to validate tool arguments for {tool_name}: {exc}",
                kind="invalid_arguments",
                tool=tool_name,
            ) from exc

        return action  # type: ignore[return-value]
