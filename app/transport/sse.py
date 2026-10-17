"""Server-Sent Events wire format: named events, comments, and heartbeats."""

from __future__ import annotations

import json
from typing import Any


def accepts_event_stream(accept: str | None) -> bool:
    """True when the client negotiated SSE via Accept: text/event-stream."""
    if not accept:
        return False
    return "text/event-stream" in accept.lower()


def format_comment(text: str) -> str:
    """SSE comment line (used for : ping heartbeats)."""
    return f": {text}\n\n"


def format_event(event: str, data: Any) -> str:
    """One named SSE event with JSON-serialized data."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def format_status_event(phase: str, label: str) -> str:
    """A progress status frame (spec §7.2): event: status / {phase,label}."""
    return format_event("status", {"phase": phase, "label": label})


def heartbeat_ping() -> str:
    return format_comment("ping")
