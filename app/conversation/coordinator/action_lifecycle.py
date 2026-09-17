"""Action admission, running, and outcome lifecycle for coordinator turns."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.conversation.coordinator.contracts import ActionKind, ActionStatus


class ActionProtocolError(RuntimeError):
    """An outcome that does not match one admitted, running action of this turn."""


@dataclass(frozen=True)
class ActionOutcome:
    turn_id: str
    action_id: str
    kind: ActionKind
    status: ActionStatus
    result: Any = None
    elapsed_ms: int | None = None


class ActionLifecycle:
    """Tracks sequential admission, start, and single terminal completion of actions in a turn."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self._admitted: dict[str, ActionKind] = {}
        self._running: dict[str, ActionKind] = {}
        self._completed: set[str] = set()
        self._outcomes: list[ActionOutcome] = []
        self._started_at: dict[str, float] = {}
        self._elapsed_ms: dict[str, int] = {}

    def admit(self, action_id: str, kind: ActionKind) -> None:
        if (
            action_id in self._admitted
            or action_id in self._running
            or action_id in self._completed
        ):
            raise ActionProtocolError(f"duplicate or already admitted action: {action_id}")
        self._admitted[action_id] = kind

    def start(self, action_id: str) -> None:
        if action_id not in self._admitted:
            raise ActionProtocolError(f"cannot start unadmitted action: {action_id}")
        kind = self._admitted.pop(action_id)
        self._running[action_id] = kind
        self._started_at[action_id] = time.perf_counter()

    def complete(self, action_id: str, outcome: ActionOutcome) -> None:
        if action_id not in self._running:
            raise ActionProtocolError(f"cannot complete action not running: {action_id}")
        if outcome.turn_id != self.turn_id or outcome.action_id != action_id:
            raise ActionProtocolError("outcome identity mismatch")
        if outcome.kind != self._running[action_id]:
            raise ActionProtocolError("outcome kind mismatch")
        del self._running[action_id]
        self._completed.add(action_id)
        if outcome.elapsed_ms is not None:
            self._elapsed_ms[action_id] = outcome.elapsed_ms
        else:
            started = self._started_at.get(action_id)
            if started is not None:
                self._elapsed_ms[action_id] = max(0, int((time.perf_counter() - started) * 1000))
        self._outcomes.append(outcome)

    def elapsed_ms(self, action_id: str) -> int | None:
        if action_id in self._elapsed_ms:
            return self._elapsed_ms[action_id]
        started = self._started_at.get(action_id)
        if started is not None:
            return max(0, int((time.perf_counter() - started) * 1000))
        return None

    def get_elapsed_ms(self, action_id: str) -> int | None:
        return self.elapsed_ms(action_id)

    def assert_ready_for_model(self) -> None:
        if self._admitted or self._running:
            raise ActionProtocolError("actions still pending or running before model step")

    @property
    def outcomes(self) -> tuple[ActionOutcome, ...]:
        return tuple(self._outcomes)
