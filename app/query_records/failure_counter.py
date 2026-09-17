"""Write-failure counter — survives OTel export failure."""

from __future__ import annotations

from opentelemetry import metrics

from app.telemetry.metrics import record_business_query_failure

_in_memory_write_failures = 0

_meter = metrics.get_meter("app.query_records")
_otel_counter = _meter.create_counter(
    "query_record.write_failures",
    unit="{failure}",
    description="Query Record insert/update failures (fail-open path).",
)


def increment_write_failure() -> None:
    """Increment at an in-memory seam first; best-effort OTel mirror."""
    global _in_memory_write_failures
    _in_memory_write_failures += 1
    record_business_query_failure(kind="query_record")
    try:
        _otel_counter.add(1)
    except Exception:
        # Counter evidence must not share the store's failure mode.
        pass


def get_write_failure_count() -> int:
    return _in_memory_write_failures


def reset_write_failure_count_for_tests() -> None:
    global _in_memory_write_failures
    _in_memory_write_failures = 0
