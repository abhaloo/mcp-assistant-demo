"""Bridge streamed provider reasoning summaries to a progress sink as thought deltas."""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler

from app.business_query.ports import BusinessProgressSink
from app.providers.reasoning import reasoning_summary_text


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
