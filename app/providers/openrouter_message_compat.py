"""Preserve OpenRouter/DeepSeek reasoning fields through LangChain ChatOpenAI.

``langchain_openai.ChatOpenAI`` intentionally drops non-OpenAI message fields
(``reasoning``, ``reasoning_details``, ``reasoning_content``). OpenRouter returns
them on the wire; tool-loop replay and our smoke oracle need them on
``AIMessage.additional_kwargs``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai.chat_models import base as lc_openai_base

REASONING_KEYS = ("reasoning_content", "reasoning", "reasoning_details")
_REASONING_KEYS = REASONING_KEYS

_patched = False
_orig_dict_to_message = lc_openai_base._convert_dict_to_message
_orig_message_to_dict = lc_openai_base._convert_message_to_dict


def _convert_dict_to_message_with_reasoning(_dict: Any) -> BaseMessage:
    msg = _orig_dict_to_message(_dict)
    if not isinstance(msg, AIMessage):
        return msg
    extras = {key: _dict[key] for key in _REASONING_KEYS if key in _dict and _dict[key] is not None}
    if not extras:
        return msg
    merged = {**(msg.additional_kwargs or {}), **extras}
    return msg.model_copy(update={"additional_kwargs": merged})


def _convert_message_to_dict_with_reasoning(
    message: BaseMessage,
    api: Any = "chat/completions",
) -> dict:
    message_dict = _orig_message_to_dict(message, api=api)
    if isinstance(message, AIMessage):
        for key in _REASONING_KEYS:
            value = (message.additional_kwargs or {}).get(key)
            if value is not None:
                message_dict[key] = value
    return message_dict


def ensure_reasoning_message_compat() -> None:
    """Idempotently patch LangChain converters used by ChatOpenAI."""
    global _patched
    if _patched:
        return
    lc_openai_base._convert_dict_to_message = _convert_dict_to_message_with_reasoning
    lc_openai_base._convert_message_to_dict = _convert_message_to_dict_with_reasoning
    _patched = True


def ensure_openrouter_reasoning_compat() -> None:
    """Alias for ``ensure_reasoning_message_compat`` (OpenRouter + DeepSeek direct)."""
    ensure_reasoning_message_compat()


def reasoning_fields_from_message(message: AIMessage) -> dict[str, Any]:
    """Return non-empty reasoning wire fields stored on an ``AIMessage``."""
    kwargs = message.additional_kwargs or {}
    return {key: kwargs[key] for key in REASONING_KEYS if kwargs.get(key) is not None}


def merge_reasoning_onto_message(message: AIMessage, raw_message: Any) -> AIMessage:
    """Copy provider reasoning fields from a raw API message dict onto ``AIMessage``."""
    if not isinstance(raw_message, dict):
        return message
    extras = {
        key: raw_message[key]
        for key in REASONING_KEYS
        if key in raw_message and raw_message[key] is not None
    }
    if not extras:
        return message
    merged = {**(message.additional_kwargs or {}), **extras}
    return message.model_copy(update={"additional_kwargs": merged})


def inject_reasoning_into_wire_messages(
    wire_messages: list[dict[str, Any]],
    source_messages: list[BaseMessage],
) -> None:
    """Replay reasoning fields on assistant wire dicts from source ``AIMessage`` rows."""
    assistant_sources = [m for m in source_messages if isinstance(m, AIMessage)]
    assistant_wire = [m for m in wire_messages if m.get("role") == "assistant"]
    for wire, source in zip(assistant_wire, assistant_sources, strict=False):
        for key, value in reasoning_fields_from_message(source).items():
            wire[key] = value
