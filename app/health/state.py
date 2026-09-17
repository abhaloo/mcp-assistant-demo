from __future__ import annotations

import time
from collections import deque

from app.config import settings


class ErrorTracker:
    def __init__(self, window_seconds: int = 60) -> None:
        self._window = window_seconds
        self._timestamps: deque[float] = deque()

    def record_500(self) -> None:
        self._timestamps.append(time.time())
        self._prune()

    def count(self) -> int:
        self._prune()
        return len(self._timestamps)

    def clear(self) -> None:
        self._timestamps.clear()

    def _prune(self) -> None:
        cutoff = time.time() - self._window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()


error_tracker = ErrorTracker(window_seconds=settings.health.error_window_seconds)


class HealthCache:
    def __init__(self) -> None:
        self._flags: dict[str, bool] = {}

    def update(self, results: dict[str, bool]) -> None:
        self._flags.update(results)

    def reset(self) -> None:
        self._flags.clear()

    def snapshot(self) -> dict[str, bool]:
        return dict(self._flags)

    def all_ok(self, keys: tuple[str, ...]) -> bool:
        return all(self._flags.get(k) for k in keys)


health_cache = HealthCache()
