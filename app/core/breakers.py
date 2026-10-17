"""Circuit breakers — the class AND the shared instances, one module.
Replaces the one-letter-apart twins circuit_breaker.py / circuit_breakers.py
(review M5). No OTel imports here: telemetry observes breakers from
app/telemetry/metrics.py, core stays dependency-free."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from enum import Enum


class BreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitBreaker:
    """Mutated from worker threads (run_in_thread) — all transitions hold the lock."""

    def __init__(self, name: str, max_failures: int, timeout: timedelta) -> None:
        self.name = name
        self.max_failures = max_failures
        self.timeout = timeout
        self.state = BreakerState.CLOSED
        self.failures = 0
        self.last_failure_time: datetime | None = None
        self._lock = threading.Lock()

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            self.last_failure_time = datetime.now()
            if self.failures >= self.max_failures:
                self.state = BreakerState.OPEN

    def can_execute(self) -> bool:
        with self._lock:
            if self.state == BreakerState.CLOSED:
                return True
            if self.last_failure_time is None:
                return True
            if datetime.now() - self.last_failure_time >= self.timeout:
                self.state = BreakerState.HALF_OPEN
                return True
            return False


retriever_breaker = CircuitBreaker("retriever", max_failures=3, timeout=timedelta(seconds=60))
sql_agent_breaker = CircuitBreaker("sql_agent", max_failures=3, timeout=timedelta(seconds=60))

ALL_BREAKERS: tuple[CircuitBreaker, ...] = (retriever_breaker, sql_agent_breaker)
