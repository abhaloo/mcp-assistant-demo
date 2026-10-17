"""Pre-call run budget for the SQL agent graph."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import tiktoken
from langchain_core.messages import BaseMessage


class SqlContextBudgetExceeded(Exception):
    """Raised by SqlRunBudget.check_before_call before a paid LLM invoke."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _message_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def estimate_messages_tokens(messages: list, *, system_content: str = "") -> int:
    """tiktoken cl100k_base over visible content + reasoning_content additional_kwargs.

    Approximate OpenAI-shaped estimate — NOT DeepSeek billed tokens. Gate is fail-closed
    on this estimate; the tokenizer gap vs provider billing is documented in settings.
    """
    enc = tiktoken.get_encoding("cl100k_base")
    total = len(enc.encode(system_content)) if system_content else 0
    for msg in messages:
        total += len(enc.encode(_message_text(msg)))
        kwargs = getattr(msg, "additional_kwargs", None) or {}
        reasoning = kwargs.get("reasoning_content")
        if reasoning:
            total += len(enc.encode(str(reasoning)))
    return total


@dataclass
class SqlRunBudget:
    max_llm_calls: int
    max_next_prompt_tokens: int
    max_cumulative_prompt_tokens: int | None = None
    max_cumulative_cost_usd: float | None = None
    # (prompt_tokens, completion_tokens) -> usd. None = token accounting only (no cost gate).
    price_fn: Callable[[int, int], float] | None = None
    _llm_calls: int = field(default=0, repr=False)
    _prompt_tokens: int = field(default=0, repr=False)
    _cost_usd: float = field(default=0.0, repr=False)
    _clarify_stop: bool = field(default=False, repr=False)

    def reset(self) -> None:
        """Clear per-turn counters. Call at invoke start — never mid-run to 'buy' more calls."""
        self._llm_calls = 0
        self._prompt_tokens = 0
        self._cost_usd = 0.0
        self._clarify_stop = False

    def mark_clarify_stop(self) -> None:
        self._clarify_stop = True

    def consume_clarify_stop(self) -> bool:
        flagged = self._clarify_stop
        self._clarify_stop = False
        return flagged

    def check_before_call(self, messages: list, *, system_content: str = "") -> None:
        """Raise SqlContextBudgetExceeded before paid invoke when over budget."""
        if self._llm_calls >= self.max_llm_calls:
            raise SqlContextBudgetExceeded("max_llm_calls")
        estimate = estimate_messages_tokens(messages, system_content=system_content)
        if estimate > self.max_next_prompt_tokens:
            raise SqlContextBudgetExceeded("next_prompt_tokens")
        if (
            self.max_cumulative_prompt_tokens is not None
            and self._prompt_tokens >= self.max_cumulative_prompt_tokens
        ):
            raise SqlContextBudgetExceeded("cumulative_tokens")
        if (
            self.max_cumulative_cost_usd is not None
            and self._cost_usd >= self.max_cumulative_cost_usd
        ):
            raise SqlContextBudgetExceeded("cumulative_cost_usd")

    def record_llm_call(self) -> None:
        """Increment call counter after a successful LLM invoke starts/returns."""
        self._llm_calls += 1

    def record_usage(self, *, prompt_tokens: int = 0, cost_usd: float = 0.0) -> None:
        """Optional post-call cumulative accounting when callbacks expose usage."""
        self._prompt_tokens += prompt_tokens
        self._cost_usd += cost_usd

    def record_response_usage(self, response) -> None:
        """Accumulate usage from a LangChain AIMessage after a completed invoke.

        Absent usage_metadata degrades to a no-op: the call counter still bounds the
        run. Never estimate tokens here -- estimate_messages_tokens is a pre-call
        gate on the NEXT prompt, not billed usage.
        """
        usage = getattr(response, "usage_metadata", None) or {}
        prompt = int(usage.get("input_tokens") or 0)
        completion = int(usage.get("output_tokens") or 0)
        if not prompt and not completion:
            return
        cost = self.price_fn(prompt, completion) if self.price_fn else 0.0
        self.record_usage(prompt_tokens=prompt, cost_usd=cost)
