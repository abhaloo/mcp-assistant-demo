"""Bridge streamed provider reasoning summaries to a progress sink as thought deltas."""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

from app.business_query.ports import BusinessProgressSink


def reasoning_summary_text(message: Any) -> str:
    """Summary text carried by one message or chunk.

    langchain-openai delivers a Responses reasoning item either as a content
    block of type ``reasoning`` or, in its v0 output shape, under
    ``additional_kwargs["reasoning"]``. Answer text blocks are never thoughts.
    """
    blocks: list[dict[str, Any]] = []
    kwargs = getattr(message, "additional_kwargs", None) or {}
    item = kwargs.get("reasoning")
    if isinstance(item, dict):
        blocks.append(item)
    content = getattr(message, "content", None)
    if isinstance(content, list):
        blocks.extend(b for b in content if isinstance(b, dict) and b.get("type") == "reasoning")
    parts: list[str] = []
    for block in blocks:
        for part in block.get("summary") or []:
            if not isinstance(part, dict) or part.get("type") != "summary_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts)


class ThoughtStreamObserver(AsyncCallbackHandler):
    """Emit each streamed reasoning summary delta to the sink.

    A route that does not stream carries the whole summary on the final message;
    that is emitted once at the end, and never again after streamed deltas.
    """

    def __init__(self, sink: BusinessProgressSink) -> None:
        self._sink = sink
        self._streamed_chars = 0

    async def on_llm_new_token(self, token: str, *, chunk: Any = None, **kwargs: Any) -> None:
        text = reasoning_summary_text(getattr(chunk, "message", None))
        if text:
            self._streamed_chars += len(text)
            self._sink.emit_thought_delta(text)

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if self._streamed_chars:
            return
        for generations in getattr(response, "generations", None) or []:
            for generation in generations:
                text = reasoning_summary_text(getattr(generation, "message", None))
                if text:
                    self._streamed_chars += len(text)
                    self._sink.emit_thought_delta(text)
