"""Progress sink that turns engine stages into Ask AI v2 activity events."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.business_query.ports import CommittedTable, ProgressStage
from app.core.errors import QueueBufferExceededError
from app.models.ask_v2_events import (
    ActivityEvent,
    ActivityKind,
    ActivityState,
    AskV2Event,
    ResultStreamAuthorizedEvent,
    ThoughtDeltaEvent,
    ThoughtDoneEvent,
    ThoughtStartEvent,
)
from app.services.ask_frames import AskStage
from app.services.ask_tables import cell_wire_bytes, paint_committed_table
from app.services.ask_v2_columns import wire_table_columns

__all__ = [
    "AskV2ProgressSink",
    "NullProgressSink",
    "TurnSequence",
    "cell_wire_bytes",
    "paint_committed_table",
]


# Legacy ask stages a person should see as a step. The rest are transport
# states the timeline does not report.
_ASK_STAGE_KIND: dict[AskStage, ActivityKind] = {
    "thinking": "thinking",
    "searching_documents": "searching_documents",
    "reading_page": "reading_page",
    "reading_next_page": "reading_page",
}


class _EventQueue(Protocol):
    def put_nowait(self, event: AskV2Event) -> None: ...


class TurnSequence:
    """The wire sequence for one turn. A number is consumed only by a frame that
    was enqueued, because the consumer rejects a gap."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def peek(self) -> int:
        return self._next

    def take(self) -> int:
        value = self._next
        self._next += 1
        return value


# Steps whose row nests the model's reasoning transcript.
_THOUGHT_HOST_KINDS: frozenset[ActivityKind] = frozenset({"thinking", "planning"})


@dataclass(frozen=True, slots=True)
class _ActiveStep:
    activity_id: str
    kind: ActivityKind
    started: float
    ordinal: int | None = None
    of: int | None = None
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class _OpenThought:
    activity_id: str
    started: float


class AskV2ProgressSink:
    """One open step at a time. A new stage closes the open one as completed.

    A thought phase belongs to the step that is open when its first delta
    arrives, and every thought frame names that step. The open step must already
    be a thought host (``thinking`` or ``planning``). If no host is open, or the
    host is not a thought host, the delta is dropped. A thought never outlives
    its step.
    """

    def __init__(
        self,
        queue: _EventQueue,
        run_id: str,
        sequence: TurnSequence,
        *,
        resumed_from_ordinals: frozenset[int] = frozenset(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue = queue
        self._run_id = run_id
        self._sequence = sequence
        self._clock = clock
        self._turn_started = clock()
        self._open: _ActiveStep | None = None
        self._authorized_seen = False
        self.disclosure_violation = False
        self._painted_ordinals: set[int] = set(resumed_from_ordinals)
        self._stream_authorized_emitted: bool = bool(resumed_from_ordinals)
        self._thought: _OpenThought | None = None
        # How often each (kind, ordinal) has opened; a repeat gets its own id so
        # a second decision is a new row, not the first one running again.
        self._step_rounds: dict[tuple[ActivityKind, int | None], int] = {}

    @property
    def painted_ordinals(self) -> frozenset[int]:
        return frozenset(self._painted_ordinals)

    def stage(self, stage: AskStage) -> None:
        kind = _ASK_STAGE_KIND.get(stage)
        if kind is not None:
            self._open_step(kind)

    def emit(
        self,
        stage: ProgressStage,
        *,
        ordinal: int | None = None,
        of: int | None = None,
        subject: str | None = None,
    ) -> None:
        self._open_step(stage, ordinal=ordinal, of=of, subject=subject)

    def table(self, section: CommittedTable) -> None:
        if not self._authorized_seen:
            self.disclosure_violation = True
            return
        if section.ordinal in self._painted_ordinals:
            return
        if not self._stream_authorized_emitted:
            auth_ev = ResultStreamAuthorizedEvent(
                protocol_version="2",
                run_id=self._run_id,
                sequence=self._sequence.peek(),
                event_type="result_stream_authorized",
            )
            if not self._enqueue(auth_ev):
                return
            self._stream_authorized_emitted = True

        raw_cols = [c.model_dump() if hasattr(c, "model_dump") else c for c in section.columns]
        columns = wire_table_columns(raw_cols)
        paint_committed_table(
            queue=self._queue,
            seq=self._sequence,
            run_id=self._run_id,
            ordinal=section.ordinal,
            rows=section.rows,
            columns=columns,
            presentation=section.presentation,
            total_row_count=section.total_row_count,
        )
        self._painted_ordinals.add(section.ordinal)

    def emit_thought_delta(self, chunk: str) -> None:
        if not chunk:
            return
        if self._thought is None:
            host = self._open
            if host is None or host.kind not in _THOUGHT_HOST_KINDS:
                return
            start_event = ThoughtStartEvent(
                protocol_version="2",
                run_id=self._run_id,
                sequence=self._sequence.peek(),
                event_type="thought_start",
                activity_id=host.activity_id,
            )
            if not self._enqueue(start_event):
                return
            self._thought = _OpenThought(activity_id=host.activity_id, started=self._clock())

        delta_event = ThoughtDeltaEvent(
            protocol_version="2",
            run_id=self._run_id,
            sequence=self._sequence.peek(),
            event_type="thought_delta",
            activity_id=self._thought.activity_id,
            delta=chunk,
        )
        self._enqueue(delta_event)

    def finish_thought(self) -> None:
        if self._thought is None:
            return
        thought = self._thought
        self._thought = None
        done_event = ThoughtDoneEvent(
            protocol_version="2",
            run_id=self._run_id,
            sequence=self._sequence.peek(),
            event_type="thought_done",
            activity_id=thought.activity_id,
            duration_ms=self._ms_since(thought.started),
        )
        self._enqueue(done_event)

    def _enqueue(self, event: AskV2Event) -> bool:
        """Hand one frame to the queue; the sequence number is spent only when
        the frame was accepted, so a dropped frame never reads as a gap."""
        try:
            self._queue.put_nowait(event)
        except (QueueBufferExceededError, RuntimeError):
            return False
        self._sequence.take()
        return True

    def fail(self) -> int:
        self.finish_thought()
        self._close("failed")
        return self._ms_since(self._turn_started)

    def finish(self) -> int:
        self.finish_thought()
        self._close("completed")
        return self._ms_since(self._turn_started)

    def _open_step(
        self,
        kind: ActivityKind,
        *,
        ordinal: int | None = None,
        of: int | None = None,
        subject: str | None = None,
    ) -> _ActiveStep:
        self._close("completed")
        if kind == "authorized":
            self._authorized_seen = True
        self._open = _ActiveStep(
            activity_id=self._next_activity_id(kind, ordinal),
            kind=kind,
            started=self._clock(),
            ordinal=ordinal,
            of=of,
            subject=subject,
        )
        self._put(self._open, "running", 0)
        return self._open

    def _close(self, state: ActivityState) -> None:
        if self._open is None:
            return
        self.finish_thought()
        step = self._open
        self._open = None
        self._put(step, state, self._ms_since(step.started))

    def _next_activity_id(self, kind: ActivityKind, ordinal: int | None) -> str:
        base = (
            f"act-{self._run_id}-{kind}-{ordinal}"
            if ordinal is not None
            else f"act-{self._run_id}-{kind}"
        )
        rounds = self._step_rounds.get((kind, ordinal), 0) + 1
        self._step_rounds[(kind, ordinal)] = rounds
        return base if rounds == 1 else f"{base}-r{rounds}"

    def _ms_since(self, started: float) -> int:
        return max(0, int(round((self._clock() - started) * 1000)))

    def _put(self, step: _ActiveStep, state: ActivityState, elapsed_ms: int) -> None:
        if step.ordinal is not None and not self._authorized_seen:
            self.disclosure_violation = True
            return
        tool_kind = "coordinator" if step.kind == "thinking" else "business_query"
        event = ActivityEvent(
            run_id=self._run_id,
            sequence=self._sequence.peek(),
            activity_id=step.activity_id,
            activity_kind=step.kind,
            tool_kind=tool_kind,
            state=state,
            elapsed_ms=elapsed_ms,
            ordinal=step.ordinal,
            of=step.of,
            subject=step.subject,
        )
        # An activity frame is the one frame the stream can afford to lose.
        # This runs inside the engine and inside the producer's error
        # handlers, where a raise would end the stream with no terminal frame.
        self._enqueue(event)


class NullProgressSink:
    """The sink the producer uses when activity events are switched off.

    Activity frames stay silent. Table frames still paint when a queue is bound.
    """

    def __init__(
        self,
        queue: _EventQueue | None = None,
        run_id: str = "",
        sequence: TurnSequence | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._queue = queue
        self._run_id = run_id
        self._sequence = sequence
        self._clock = clock
        self._turn_started = clock()
        self.disclosure_violation = False
        self._authorized_seen = False
        self._painted_ordinals: set[int] = set()
        self._stream_authorized_emitted = False

    @property
    def painted_ordinals(self) -> frozenset[int]:
        return frozenset(self._painted_ordinals)

    def stage(self, stage: AskStage) -> None:
        return

    def emit(
        self,
        stage: ProgressStage,
        *,
        ordinal: int | None = None,
        of: int | None = None,
        subject: str | None = None,
    ) -> None:
        _ = (ordinal, of, subject)
        if stage == "authorized":
            self._authorized_seen = True
        return

    def table(self, section: CommittedTable) -> None:
        if self._queue is None or self._sequence is None:
            return
        if not self._authorized_seen:
            self.disclosure_violation = True
            return
        if section.ordinal in self._painted_ordinals:
            return
        if not self._stream_authorized_emitted:
            auth_ev = ResultStreamAuthorizedEvent(
                protocol_version="2",
                run_id=self._run_id,
                sequence=self._sequence.peek(),
                event_type="result_stream_authorized",
            )
            self._queue.put_nowait(auth_ev)
            self._sequence.take()
            self._stream_authorized_emitted = True
        raw_cols = [c.model_dump() if hasattr(c, "model_dump") else c for c in section.columns]
        columns = wire_table_columns(raw_cols)
        paint_committed_table(
            queue=self._queue,
            seq=self._sequence,
            run_id=self._run_id,
            ordinal=section.ordinal,
            rows=section.rows,
            columns=columns,
            presentation=section.presentation,
            total_row_count=section.total_row_count,
        )
        self._painted_ordinals.add(section.ordinal)

    def emit_thought_delta(self, chunk: str) -> None:
        _ = chunk
        return

    def finish_thought(self) -> None:
        return

    def fail(self) -> int:
        return self.finish()

    def finish(self) -> int:
        return max(0, int(round((self._clock() - self._turn_started) * 1000)))
