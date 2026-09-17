"""Deadline-probe arm clocks. Telemetry does not own these deadlines."""

from __future__ import annotations

ARM_PRESETS: dict[str, dict[str, float | int | bool | None]] = {
    "current": {
        "deadline_ms": 25_000,
        "http_timeout_s": 30.0,
        "unbounded": False,
        "max_deadline_ms": 25_000,
        "planner": 18.0,
    },
    "plus50": {
        "deadline_ms": 37_500,
        "http_timeout_s": 45.0,
        "unbounded": False,
        "max_deadline_ms": 37_500,
        "planner": 27.0,
    },
    "unbounded": {
        "deadline_ms": 480_000,
        "http_timeout_s": 480.0,
        "unbounded": True,
        "max_deadline_ms": 25_000,
        "planner": None,
    },
}
