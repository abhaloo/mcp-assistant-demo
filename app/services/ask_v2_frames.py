"""Ask AI v2 SSE frame wire encoding and sequence validation."""

from __future__ import annotations

from enum import StrEnum

from app.models.ask_v2_events import AskV2EventBase

TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({"turn_outcome", "stream_error"})

KNOWN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "activity",
        "text_delta",
        "thought_start",
        "thought_delta",
        "thought_done",
        "result_stream_authorized",
        "table_start",
        "table_rows",
        "table_end",
        "record_details",
        "interaction",
        "turn_outcome",
        "stream_error",
    }
)


class SequenceOutcome(StrEnum):
    """Result of classifying one event against the v2 sequence contract."""

    ACCEPT = "accept"
    """Strictly the next expected event: emit it and advance."""

    IGNORE = "ignore"
    """An exact resend of the last accepted event: drop it silently."""

    REJECT = "reject"
    """A conflicting duplicate, a gap, data after terminal, or an unknown
    event kind: the caller must fail the stream closed."""


class V2EventSequenceValidator:
    """Classifies each event against strictly monotonic sequencing and terminal semantics.

    An exact resend of the last accepted event (same sequence, identical
    payload) is ignored rather than treated as an error, matching at-least-
    once delivery. Anything else out of order is rejected.
    """

    def __init__(self, initial_sequence: int | None = None) -> None:
        self._expected_sequence: int | None = initial_sequence
        self._terminal_reached: bool = False
        self._last_event: AskV2EventBase | None = None

    @property
    def terminal_reached(self) -> bool:
        return self._terminal_reached

    def validate_next(self, event: AskV2EventBase) -> SequenceOutcome:
        """Classify the next event: accept, ignore (exact duplicate), or reject."""
        if event.event_type not in KNOWN_EVENT_TYPES:
            return SequenceOutcome.REJECT

        if self._terminal_reached:
            return SequenceOutcome.REJECT

        if self._expected_sequence is None:
            self._expected_sequence = event.sequence + 1
        elif event.sequence == self._expected_sequence - 1:
            if self._last_event is not None and event == self._last_event:
                return SequenceOutcome.IGNORE
            return SequenceOutcome.REJECT
        elif event.sequence != self._expected_sequence:
            return SequenceOutcome.REJECT
        else:
            self._expected_sequence += 1

        self._last_event = event
        if event.event_type in TERMINAL_EVENT_TYPES:
            self._terminal_reached = True
        return SequenceOutcome.ACCEPT


def render_v2_sse_frame(event: AskV2EventBase) -> str:
    """Render an Ask AI v2 event as an SSE wire frame."""
    return f"event: {event.event_type}\ndata: {event.model_dump_json()}\n\n"


# An SSE comment line. Parsers drop it; it carries no event and no sequence.
KEEP_ALIVE_FRAME = ": keep-alive\n\n"
