"""Provider context counter and token/byte measurement budget."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from langchain_core.messages import BaseMessage

if TYPE_CHECKING:
    from app.providers.capability_model import CapabilityChatModel


def _capability_error(model: str, capability: str) -> Exception:
    from app.providers.model_registry import CapabilityError

    return CapabilityError(model, capability)


@dataclass(frozen=True)
class ContextProfile:
    """Declared per catalog target (`DeploymentTarget.context_profile`, new optional field)."""

    context_window_tokens: int
    counting_profile: Literal["openai-tiktoken"]
    framing_tokens_per_message: int = 4


@dataclass(frozen=True)
class ContextMeasure:
    tokens: int  # conservative upper bound
    bytes: int  # exact UTF-8 length of the serialised messages plus tool schemas


class ProviderContextCounter:
    """Measures messages and tool schemas with conservative token bound + exact UTF-8 bytes."""

    def __init__(self, chat_model: CapabilityChatModel, profile: ContextProfile) -> None:
        if profile.counting_profile != "openai-tiktoken":
            raise _capability_error(
                chat_model.spec.name,
                f"unsupported counting_profile: {profile.counting_profile!r}",
            )
        self._chat_model = chat_model
        self._profile = profile

    @property
    def capacity_tokens(self) -> int:
        return self._profile.context_window_tokens

    def measure(
        self,
        messages: Sequence[BaseMessage],
        tools: Sequence[dict[str, object]],
    ) -> ContextMeasure:
        if not messages and not tools:
            return ContextMeasure(tokens=0, bytes=0)

        total_bytes = 0
        for msg in messages:
            if isinstance(msg.content, str):
                total_bytes += len(msg.content.encode("utf-8"))
            else:
                total_bytes += len(json.dumps(msg.content, separators=(",", ":")).encode("utf-8"))

        for tool in tools:
            total_bytes += len(json.dumps(tool, separators=(",", ":")).encode("utf-8"))

        total_tokens = 0
        if messages:
            if hasattr(self._chat_model.inner, "get_num_tokens_from_messages"):
                total_tokens += self._chat_model.inner.get_num_tokens_from_messages(list(messages))
            else:
                for m in messages:
                    c = m.content if isinstance(m.content, str) else json.dumps(m.content)
                    total_tokens += 3 + self._chat_model.inner.get_num_tokens(c)
                total_tokens += 3

            total_tokens += len(messages) * self._profile.framing_tokens_per_message

        for tool in tools:
            serialized = json.dumps(tool, separators=(",", ":"))
            total_tokens += self._chat_model.inner.get_num_tokens(serialized)

        return ContextMeasure(tokens=total_tokens, bytes=total_bytes)

    def self_test(self) -> None:
        """Startup check: tokenizer of fixture must equal recorded count, else CapabilityError."""
        # KNOWN_TOKEN_FIXTURE = ("The quick brown fox jumps over the lazy dog.", 10, 44)
        fixture_text = "The quick brown fox jumps over the lazy dog."
        expected_tokens = 10
        expected_bytes = 44

        actual_tokens = self._chat_model.inner.get_num_tokens(fixture_text)
        actual_bytes = len(fixture_text.encode("utf-8"))

        if actual_tokens != expected_tokens or actual_bytes != expected_bytes:
            raise _capability_error(
                self._chat_model.spec.name,
                f"context budget self_test failed: expected ({expected_tokens} tokens, "
                f"{expected_bytes} bytes), got ({actual_tokens} tokens, {actual_bytes} bytes)",
            )
