"""OpenAI-compatible chat provider — Foundry /openai/v1/, DeepSeek direct, OpenRouter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

if TYPE_CHECKING:
    from app.resources import ProcessResources

from app.config import settings
from app.providers.deepseek_direct_controls import (
    DeepSeekDirectControls,
    build_deepseek_direct_extra,
)
from app.providers.http_clients import get_async_http_client, get_sync_http_client
from app.providers.model_registry import ModelSpec
from app.providers.openrouter_controls import OpenRouterControls, build_provider_extra
from app.providers.openrouter_message_compat import (
    ensure_reasoning_message_compat,
    inject_reasoning_into_wire_messages,
    merge_reasoning_onto_message,
)
from app.providers.reasoning_effort_policy import assert_effort_supported

# Readable summary of the model's reasoning on the Responses API; gpt-5 / Luna
# reject "concise".
OPENAI_REASONING_SUMMARY = "detailed"


class DeepSeekDirectChatOpenAI(ChatOpenAI):
    """ChatOpenAI that preserves/replays DeepSeek ``reasoning_content`` in tool loops.

    LangChain's stock converters intentionally drop non-OpenAI fields. Thinking mode
    requires those fields on both ingest and the next outbound request or the API
    returns 400. We keep the shared monkeypatch for OpenRouter parity and also
    enforce replay here so direct calls do not depend on patch ordering.

    Thinking mode also rejects ``tool_choice="any"``; map it to ``auto`` on wire.
    Synthetic graph seed turns (``list_tables``) never hit the API — inject empty
    ``reasoning_content`` on assistant tool-call rows when missing.
    """

    def _thinking_enabled(self) -> bool:
        body = self.extra_body or {}
        thinking = body.get("thinking")
        return isinstance(thinking, dict) and thinking.get("type") == "enabled"

    def _ensure_thinking_wire_compat(self, payload: dict[str, Any]) -> None:
        if not self._thinking_enabled():
            return
        if payload.get("tool_choice") in ("any", "required"):
            payload["tool_choice"] = "auto"
        for message in payload.get("messages") or []:
            if message.get("role") != "assistant":
                continue
            if not message.get("tool_calls"):
                continue
            if message.get("reasoning_content") is None:
                message["reasoning_content"] = ""

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        source_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if self._use_responses_api(payload):
            return payload
        inject_reasoning_into_wire_messages(payload.get("messages", []), source_messages)
        self._ensure_thinking_wire_compat(payload)
        return payload

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ):
        result = super()._create_chat_result(response, generation_info=generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices") or []
        for generation, choice in zip(result.generations, choices, strict=False):
            message = generation.message
            if isinstance(message, AIMessage):
                generation.message = merge_reasoning_onto_message(
                    message,
                    choice.get("message"),
                )
        return result


class OpenRouterChatOpenAI(ChatOpenAI):
    """ChatOpenAI that keeps the legacy ``max_tokens`` wire parameter.

    ``ChatOpenAI._get_request_payload`` renames ``max_tokens`` →
    ``max_completion_tokens`` (OpenAI's September-2024 deprecation). OpenRouter
    provider endpoints declare support for ``max_tokens`` only, so with
    ``require_parameters=True`` the renamed field matches zero providers and
    every call fails 404 "No endpoints found".
    """

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload


def get_chat_model(
    spec: ModelSpec,
    *,
    temperature: float = 0.1,
    api_key: str | Callable[[], str],
    openrouter_controls: OpenRouterControls | None = None,
    deepseek_direct_controls: DeepSeekDirectControls | None = None,
    extra_body: dict | None = None,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    use_responses_api: bool = False,
    reasoning_summary: bool = False,
    resources: ProcessResources | None = None,
) -> ChatOpenAI:
    """Return a bare ChatOpenAI configured from a ModelSpec.

    Caller supplies ``api_key`` (static key or Entra token-provider callable).
    Secrets never live on ModelSpec.

    When ``credential_source=openrouter``, controls are required and always win
    for the ``provider`` key — raw ``extra_body`` cannot drop or empty them.
    When ``credential_source=deepseek_direct``, ``deepseek_direct_controls`` is
    required. Reasoning fields are preserved on AIMessage.additional_kwargs.
    When ``credential_source=openai``, ``reasoning_effort`` is asserted against
    the OpenAI-direct table and sent on the Responses path. ``reasoning_summary``
    asks that path for a readable summary of the reasoning and streams the
    reply, because the summary reaches callbacks only while streaming.
    """
    if spec.credential_source == "deepseek_direct":
        ensure_reasoning_message_compat()
        if deepseek_direct_controls is None:
            raise ValueError(
                "deepseek_direct_controls required when credential_source=deepseek_direct"
            )
        resolved_extra_body = build_deepseek_direct_extra(deepseek_direct_controls)
    elif spec.credential_source == "openrouter":
        ensure_reasoning_message_compat()
        if openrouter_controls is None:
            raise ValueError("openrouter_controls required when credential_source=openrouter")
        controls_body = build_provider_extra(openrouter_controls)
        if extra_body is None:
            resolved_extra_body = controls_body
        else:
            # Controls always win for provider pin and frozen reasoning effort.
            merged = dict(extra_body)
            merged["provider"] = controls_body["provider"]
            if "reasoning" in controls_body:
                merged["reasoning"] = controls_body["reasoning"]
            else:
                merged.pop("reasoning", None)
            resolved_extra_body = merged
    elif extra_body is not None:
        resolved_extra_body = extra_body
    elif openrouter_controls is not None:
        resolved_extra_body = build_provider_extra(openrouter_controls)
    else:
        resolved_extra_body = None

    model_cls = (
        OpenRouterChatOpenAI
        if spec.credential_source == "openrouter"
        else DeepSeekDirectChatOpenAI
        if spec.credential_source == "deepseek_direct"
        else ChatOpenAI
    )
    resolved_timeout = (
        request_timeout_s if request_timeout_s is not None else settings.model_request_timeout_s
    )
    kwargs: dict = {
        "model": spec.model_id,
        "temperature": temperature,
        "openai_api_key": api_key,
        "base_url": spec.api_base,
        "max_retries": (max_retries if max_retries is not None else settings.model_max_retries),
        "timeout": resolved_timeout,
        "http_client": get_sync_http_client(resources),
        "http_async_client": get_async_http_client(resources),
    }
    if spec.credential_source == "openai":
        kwargs["stream_usage"] = settings.chat_stream_usage
        if reasoning_effort is not None:
            assert_effort_supported(spec.model_id, reasoning_effort, provider="openai")
            if use_responses_api and reasoning_summary:
                kwargs["reasoning"] = {
                    "effort": reasoning_effort,
                    "summary": OPENAI_REASONING_SUMMARY,
                }
                kwargs["streaming"] = True
            else:
                kwargs["reasoning_effort"] = reasoning_effort
            if temperature != 1.0:
                kwargs["temperature"] = 1.0
        if verbosity is not None:
            kwargs["verbosity"] = verbosity
        if use_responses_api:
            kwargs["use_responses_api"] = True
    if resolved_extra_body is not None:
        kwargs["extra_body"] = resolved_extra_body

    return model_cls(**kwargs)
