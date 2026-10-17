"""Eval-only Azure OpenAI Responses API chat model for SQL LangGraph agents."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from app.config import settings
from app.providers.azure_credential import get_token_provider
from app.providers.model_registry import (
    normalize_openai_v1_base,
    token_scope_for_openai_v1_base,
)
from app.providers.reasoning import REASONING_KIND_AZURE_SUMMARY

# Azure Responses is on the Foundry v1 surface (`/openai/v1/responses`), not the
# Chat Completions deployment path (`/openai/deployments/...`). MS docs require
# `api-version=preview` on that route — distinct from settings.azure_api_version.
AZURE_RESPONSES_API_VERSION = "preview"
# AUP-supported readable reasoning path (gpt-5 / Luna reject "concise").
AZURE_REASONING_SUMMARY = "detailed"


def _message_to_responses_input(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        role = "user"
        if message.type == "ai":
            role = "assistant"
        elif message.type == "system":
            role = "system"
        content = message.content if isinstance(message.content, str) else str(message.content)
        payload.append({"role": role, "content": content})
    return payload


def _item_type(item: Any) -> str | None:
    item_type = getattr(item, "type", None)
    if item_type is None and isinstance(item, dict):
        item_type = item.get("type")
    return item_type


def _item_field(item: Any, name: str) -> Any:
    value = getattr(item, name, None)
    if value is None and isinstance(item, dict):
        value = item.get(name)
    return value


def _items_of_type(response: Any, item_type: str) -> list[Any]:
    return [item for item in getattr(response, "output", []) or [] if _item_type(item) == item_type]


def _extract_output_text(response: Any) -> str:
    return "".join(
        str(_item_field(part, "text") or "")
        for item in _items_of_type(response, "message")
        for part in _item_field(item, "content") or []
        if _item_type(part) == "output_text"
    )


def _extract_tool_calls(response: Any) -> list[dict[str, Any]]:
    tool_calls: list[dict[str, Any]] = []
    for item in _items_of_type(response, "function_call"):
        name = str(_item_field(item, "name") or "")
        call_id = str(_item_field(item, "call_id") or _item_field(item, "id") or "")
        arguments = _item_field(item, "arguments") or "{}"
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            args = {"raw": arguments}
        tool_calls.append(
            {
                "name": name,
                "args": args,
                "id": call_id or f"call_{len(tool_calls)}",
                "type": "tool_call",
            }
        )
    return tool_calls


def _extract_reasoning_summary(response: Any) -> str | None:
    """Join Azure Responses ``reasoning`` item summary_text parts (AUP-supported)."""
    parts: list[str] = []
    for item in _items_of_type(response, "reasoning"):
        for part in _item_field(item, "summary") or []:
            if _item_type(part) == "summary_text":
                text = str(_item_field(part, "text") or "").strip()
                if text:
                    parts.append(text)
    if not parts:
        return None
    return "\n".join(parts)


def _normalize_tool_choice_for_responses(tool_choice: Any) -> Any:
    """Map LangGraph/Chat Completions tool_choice to Azure Responses wire values."""
    if tool_choice == "any":
        return "required"
    return tool_choice


def _flatten_tools_for_responses(tools: list[Any]) -> list[dict[str, Any]]:
    """Map Chat Completions tool dicts to Azure Responses flat function schema."""
    flattened: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            flattened.append(tool)
            continue
        fn = tool.get("function")
        if isinstance(fn, dict) and "name" in fn:
            flat: dict[str, Any] = {"type": tool.get("type", "function"), "name": fn["name"]}
            if "description" in fn:
                flat["description"] = fn["description"]
            if "parameters" in fn:
                flat["parameters"] = fn["parameters"]
            flattened.append(flat)
        else:
            flattened.append(tool)
    return flattened


class AzureResponsesSqlChatModel(BaseChatModel):
    """LangGraph-compatible Azure `/v1/responses` chat model for SQL eval only."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Any = Field(default=None, exclude=True)
    deployment: str
    model: str
    api_version: str = Field(default=AZURE_RESPONSES_API_VERSION)
    request_timeout_s: float | None = None
    reasoning_effort: str | None = None

    @property
    def _llm_type(self) -> str:
        return "azure-responses-sql"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        client = self.client or self._default_client()
        request: dict[str, Any] = {
            "model": self.model,
            "input": _message_to_responses_input(messages),
        }
        tools = kwargs.get("tools")
        if tools:
            request["tools"] = _flatten_tools_for_responses(tools)
        tool_choice = kwargs.get("tool_choice")
        if tool_choice is not None:
            request["tool_choice"] = _normalize_tool_choice_for_responses(tool_choice)
        reasoning = kwargs.get("reasoning_effort", self.reasoning_effort)
        if reasoning is not None:
            request["reasoning"] = {
                "effort": reasoning,
                "summary": AZURE_REASONING_SUMMARY,
            }

        response = client.responses.create(**request)
        text = _extract_output_text(response)
        tool_calls = _extract_tool_calls(response)
        summary = _extract_reasoning_summary(response)
        usage = getattr(response, "usage", None)
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            total_tokens = int(usage.get("total_tokens") or 0)
        else:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        additional_kwargs: dict[str, Any] = {}
        response_metadata: dict[str, Any] = {
            "model": getattr(response, "model", self.model),
            "request_id": getattr(response, "id", None),
        }
        if summary:
            # Same key DeepSeek uses so CapturingCallback / extract_reasoning_evidence work.
            # reasoning_kind keeps study provenance honest (summary ≠ provider CoT).
            additional_kwargs["reasoning_content"] = summary
            response_metadata["reasoning_kind"] = REASONING_KIND_AZURE_SUMMARY
        ai = AIMessage(
            content=text,
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
            additional_kwargs=additional_kwargs,
            response_metadata=response_metadata,
        )
        return ChatResult(generations=[ChatGeneration(message=ai)])

    def _default_client(self) -> Any:
        from openai import AzureOpenAI

        if not settings.azure_endpoint:
            raise RuntimeError("azure_endpoint is required for Azure Responses SQL eval")
        base_url = normalize_openai_v1_base(settings.azure_endpoint)
        token_scope = token_scope_for_openai_v1_base(base_url)
        return AzureOpenAI(
            base_url=base_url,
            azure_ad_token_provider=get_token_provider(token_scope),
            api_version=self.api_version,
            timeout=self.request_timeout_s or settings.model_request_timeout_s,
            max_retries=settings.model_max_retries,
        )

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any):
        """Bind LangGraph SQL tools; forwards tool_choice to Responses create()."""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        formatted_tools = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(
            tools=formatted_tools,
            tool_choice=tool_choice,
            **kwargs,
        )
