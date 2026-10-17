"""Transport-neutral seams the ask flow hands to whichever adapter drives it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

# True once the caller has gone away. The SSE adapter wires this to the live
# HTTP connection; the JSON adapter has no mid-flight disconnect signal and
# passes a callable that always answers False.
Disconnected = Callable[[], Awaitable[bool]]

# Heartbeat pacing is a business decision. The wire bytes for a ping live
# in the SSE adapter.
HEARTBEAT_INTERVAL_SECONDS = 15

AskStage = Literal[
    "connecting",
    "thinking",
    "searching_documents",
    "searching_database",
    "searching_both",
    "reading_page",
    "reading_next_page",
    "writing_query",
    "writing_answer",
]


@dataclass(frozen=True)
class StatusFrame:
    stage: AskStage


@dataclass(frozen=True)
class DataFrame:
    event: str
    payload: object


@dataclass(frozen=True)
class TerminalFrame:
    event: Literal["done", "error"]
    payload: dict[str, Any]


@dataclass(frozen=True)
class HeartbeatFrame:
    pass


AskFrame = StatusFrame | DataFrame | TerminalFrame | HeartbeatFrame
