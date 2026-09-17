"""Deadline validation and budget calculations for Ask AI requests."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.core.errors import DeadlineExceededError, DeadlineExpiredError

DEADLINE_CLOCK_SKEW_MS: int = 1_000
MAX_DEADLINE_MS: int = 25_000
MAX_FUTURE_SKEW_MS: int = 26_000
UNBOUNDED_REMAINING_MS: int = 1_000_000_000


def _default_clock_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class Deadline:
    """Validated Ask deadline. Elapsed time uses the monotonic clock."""

    deadline_at_ms: int
    server_received_at_ms: int
    _monotonic_deadline_s: float
    _monotonic_clock: Callable[[], float] = time.monotonic
    _clock_fn: Callable[[], int] = _default_clock_ms

    @property
    def remaining_ms(self) -> int:
        if self.remaining_seconds == float("inf"):
            return UNBOUNDED_REMAINING_MS
        return max(0, int(self.remaining_seconds * 1000))

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self._monotonic_deadline_s - self._monotonic_clock())

    @property
    def is_expired(self) -> bool:
        return self.remaining_seconds <= 0

    def check_not_expired(self) -> None:
        """Raise DeadlineExpiredError if the deadline has expired."""
        if self.is_expired:
            now_ms = self._clock_fn()
            raise DeadlineExpiredError(
                f"Deadline expired: deadline_at_ms={self.deadline_at_ms}, now_ms={now_ms}"
            )

    @classmethod
    def with_clocks(
        cls,
        *,
        deadline_at_ms: int,
        server_received_at_ms: int,
        monotonic_deadline_s: float,
        monotonic_clock: Callable[[], float],
        wall_clock: Callable[[], int] | None = None,
    ) -> Deadline:
        """Dual-clock test seam: remaining time follows ``monotonic_clock`` only."""
        return cls(
            deadline_at_ms=deadline_at_ms,
            server_received_at_ms=server_received_at_ms,
            _monotonic_deadline_s=monotonic_deadline_s,
            _monotonic_clock=monotonic_clock,
            _clock_fn=wall_clock if wall_clock is not None else _default_clock_ms,
        )


def validate_client_deadline(
    deadline_at_ms: int,
    *,
    server_now_ms: int | None = None,
    clock_fn: Callable[[], int] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
    max_deadline_ms: int | None = None,
    max_future_skew_ms: int | None = None,
    unbounded: bool | None = None,
) -> Deadline:
    """Validate the client epoch once, then enforce elapsed time monotonically.

    ``server_now_ms`` is an ingress timestamp used only to validate and clamp
    the deadline. The clamped epoch stays on the object for wire diagnostics.
    Remaining time follows ``monotonic_clock`` after this call returns.
    """
    effective_clock = clock_fn if clock_fn is not None else _default_clock_ms
    now_ms = server_now_ms if server_now_ms is not None else effective_clock()
    mono = monotonic_clock if monotonic_clock is not None else time.monotonic

    effective_unbounded = unbounded
    effective_max_deadline_ms = max_deadline_ms
    effective_max_future_skew_ms = max_future_skew_ms
    if (
        effective_unbounded is None
        or effective_max_deadline_ms is None
        or effective_max_future_skew_ms is None
    ):
        from app.config import settings

        if effective_unbounded is None:
            effective_unbounded = settings.ask_turn_unbounded
        if effective_max_deadline_ms is None:
            effective_max_deadline_ms = settings.ask_max_deadline_ms
        if effective_max_future_skew_ms is None:
            effective_max_future_skew_ms = effective_max_deadline_ms + DEADLINE_CLOCK_SKEW_MS

    if deadline_at_ms <= (now_ms - DEADLINE_CLOCK_SKEW_MS):
        raise DeadlineExpiredError(
            f"Client deadline {deadline_at_ms} is expired "
            f"(server_now={now_ms}, skew={DEADLINE_CLOCK_SKEW_MS})"
        )

    if not effective_unbounded and deadline_at_ms > (now_ms + effective_max_future_skew_ms):
        raise DeadlineExceededError(
            f"Client deadline {deadline_at_ms} exceeds max future skew "
            f"(server_now={now_ms}, max_future={effective_max_future_skew_ms})"
        )

    if effective_unbounded:
        return Deadline(
            deadline_at_ms=deadline_at_ms,
            server_received_at_ms=now_ms,
            _monotonic_deadline_s=float("inf"),
            _monotonic_clock=mono,
            _clock_fn=effective_clock,
        )

    clamped_deadline_at_ms = min(deadline_at_ms, now_ms + effective_max_deadline_ms)
    accepted_seconds = max(0.0, (clamped_deadline_at_ms - now_ms) / 1000.0)

    return Deadline(
        deadline_at_ms=clamped_deadline_at_ms,
        server_received_at_ms=now_ms,
        _monotonic_deadline_s=mono() + accepted_seconds,
        _monotonic_clock=mono,
        _clock_fn=effective_clock,
    )


def clamp_turn_budget(
    *,
    clock_fn: Callable[[], int] | None = None,
    monotonic_clock: Callable[[], float] | None = None,
) -> Deadline:
    """Build the 25-second Ask turn budget when the caller has no client epoch."""
    effective_clock = clock_fn if clock_fn is not None else _default_clock_ms
    now_ms = effective_clock()
    return validate_client_deadline(
        now_ms + MAX_DEADLINE_MS,
        server_now_ms=now_ms,
        clock_fn=effective_clock,
        monotonic_clock=monotonic_clock,
    )
