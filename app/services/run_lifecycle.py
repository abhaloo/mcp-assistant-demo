"""Shared trace and terminal lifecycle for JSON and SSE Ask attempts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from app.services.ask_frames import AskFrame, TerminalFrame

RunOutcome = Literal["completed", "stopped", "error"]


def operational_trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Never export questions, page records, profile data, or model payloads."""
    body = inputs.get("body")
    principal = inputs.get("principal")
    return {
        "operation": getattr(body, "operation", "ask"),
        "run_id": getattr(body, "run_id", None),
        "has_thread": bool(getattr(body, "thread_id", None)),
        "context_mode": "jobs" if getattr(body, "page_context", None) is not None else "none",
        "principal": str(getattr(principal, "user_id", "")),
    }


def operational_trace_outputs(outputs: Any) -> dict[str, Any]:
    """Keep only lifecycle state at the hosted trace boundary."""
    if isinstance(outputs, dict):
        return {
            key: outputs.get(key)
            for key in ("outcome", "query_type", "committed")
            if key in outputs
        }
    return {"outcome": "completed"}


def finalize_trace(
    run_tree: Any,
    outcome: RunOutcome,
    *,
    query_type: str | None = None,
    committed: bool = False,
) -> None:
    """Stamp the root before the traceable wrapper closes it."""
    if run_tree is None:
        return
    metadata = getattr(run_tree, "metadata", None)
    if metadata is None:
        metadata = {}
        run_tree.metadata = metadata
    metadata["outcome"] = outcome
    metadata["committed"] = committed
    if query_type is not None:
        metadata["query_type"] = query_type


@dataclass
class StreamTerminal:
    """Exactly-once terminal frame and queue closure."""

    queue: asyncio.Queue[AskFrame | None]
    terminal: str | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def claim_done(self) -> bool:
        """Reserve the single finalization/terminal path."""
        async with self._lock:
            if self.terminal is not None:
                return False
            self.terminal = "finalizing"
            return True

    async def release_done(self) -> None:
        async with self._lock:
            if self.terminal == "finalizing":
                self.terminal = None

    async def emit(self, event: Literal["done", "error"], payload: dict[str, Any]) -> bool:
        async with self._lock:
            if self.terminal == "finalizing" and event == "done":
                self.terminal = "done"
            elif self.terminal is not None:
                return False
            else:
                self.terminal = event
            await self.queue.put(TerminalFrame(event, payload))
            return True

    async def close(self) -> None:
        await self.queue.put(None)
