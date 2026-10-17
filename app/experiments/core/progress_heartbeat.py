"""Coalesce experiment progress heartbeats to reduce disk churn."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

from app.experiments.core.atomic_io import atomic_write_text

WriteFn = Callable[[Path, dict[str, Any]], None]

_logger = logging.getLogger(__name__)


def write_progress_heartbeat(path: Path, progress: dict[str, Any]) -> None:
    """Write one heartbeat snapshot (campaign CLI and live eval share this)."""
    document = {
        **progress,
        "updated_at": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
    }
    atomic_write_text(
        path,
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


class CoalescedHeartbeatWriter:
    """Write heartbeat snapshots at most ~once per second, plus on stage changes."""

    def __init__(
        self,
        path: Path,
        *,
        write_fn: WriteFn,
        min_interval_s: float = 1.0,
    ) -> None:
        self._path = path
        self._write_fn = write_fn
        self._min_interval_s = min_interval_s
        self._lock = Lock()
        self._last_stage: str | None = None
        self._last_write_monotonic: float | None = None
        self._pending_state: dict[str, Any] | None = None

    def update(self, state: dict[str, Any], *, extra: dict[str, Any] | None = None) -> None:
        """Merge ``extra`` into ``state``, keep the latest snapshot, maybe flush."""
        merged = {**(extra or {}), **state}
        with self._lock:
            self._pending_state = merged
            if self._should_flush_locked(merged):
                self._flush_locked(merged)

    def flush(self) -> None:
        """Force the latest pending snapshot to disk."""
        with self._lock:
            if self._pending_state is None:
                return
            self._flush_locked(self._pending_state)

    def flush_best_effort(self) -> None:
        """Flush pending state without raising — for teardown after a successful attempt."""
        try:
            self.flush()
        except Exception as exc:  # noqa: BLE001 - exit cleanup must not discard attempt result
            _logger.warning("heartbeat flush failed during teardown: %s", exc)

    def _should_flush_locked(self, state: dict[str, Any]) -> bool:
        stage = state.get("stage")
        if stage is not None and stage != self._last_stage:
            return True
        if self._last_write_monotonic is None:
            return True
        return (monotonic() - self._last_write_monotonic) >= self._min_interval_s

    def _flush_locked(self, state: dict[str, Any]) -> None:
        # Propagate write failures — a failed heartbeat must not look alive.
        self._write_fn(self._path, state)
        self._last_write_monotonic = monotonic()
        stage = state.get("stage")
        if stage is not None:
            self._last_stage = str(stage)
