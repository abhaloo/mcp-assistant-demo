"""SSE adapter: turns the ask flow's typed frames into wire bytes.

This is the ingress façade for Ask SSE. It imports one business symbol
(`stream_ask_frames`) and serializes frames by shape, not by importing
frame classes from `app.services`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from app.auth import Principal
from app.models.schemas import Question
from app.resources import ProcessResources
from app.services.ask_stream import stream_ask_frames
from app.transport.sse import format_event, format_status_event, heartbeat_ping

Disconnected = Callable[[], Awaitable[bool]]

# The client maps phase strings to icons and labels, so every value here is a
# contract, not a log line.
_STAGE_WIRE: dict[str, tuple[str, str]] = {
    "connecting": ("connecting", "Analyzing request..."),
    "searching_documents": ("searching_documents", "Searching the documents"),
    "searching_database": ("searching_database", "Looking at the database"),
    "searching_both": ("searching_both", "Searching the documents and database"),
    "reading_page": ("searching_database", "Reading the jobs on this page"),
    "reading_next_page": ("searching_database", "Reading the next page"),
    "writing_query": ("writing_query", "Writing the query"),
    "writing_answer": ("writing_answer", "Writing the answer"),
}


def render_sse(frame: object) -> str:
    name = type(frame).__name__
    if name == "StatusFrame":
        phase, label = _STAGE_WIRE[getattr(frame, "stage")]
        return format_status_event(phase, label)
    if name == "DataFrame":
        return format_event(getattr(frame, "event"), getattr(frame, "payload"))
    if name == "TerminalFrame":
        return format_event(getattr(frame, "event"), getattr(frame, "payload"))
    if name == "HeartbeatFrame":
        return heartbeat_ping()
    raise AssertionError(f"unrenderable frame: {frame!r}")


async def stream_ask_events(
    body: Question,
    principal: Principal,
    disconnected: Disconnected,
    *,
    resources: ProcessResources,
) -> AsyncIterator[str]:
    async for frame in stream_ask_frames(body, principal, disconnected, resources=resources):
        yield render_sse(frame)
