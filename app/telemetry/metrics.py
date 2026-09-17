"""OpenTelemetry metrics — GenAI token usage, operation latency, request counts.

Mirrors the trace layer: instruments are created against the global *proxy*
meter at import and resolve to the real MeterProvider once ``setup`` configures
one (same lazy-binding contract as ``trace.get_tracer``).

CARDINALITY DISCIPLINE: ``role`` / ``permissions`` / ``access_tiers`` stay
span-only and MUST NOT appear as metric attributes — each distinct value spawns
a new time series, so high-cardinality dimensions belong on spans, not metrics.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from opentelemetry import metrics

_logger = logging.getLogger(__name__)

# Spec-mandated explicit bucket boundaries for gen_ai.client.token.usage.
# https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/
GENAI_TOKEN_BUCKETS: list[float] = [
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
]

_meter = metrics.get_meter("app.telemetry")

_token_usage = _meter.create_histogram(
    "gen_ai.client.token.usage",
    unit="{token}",
    description="Tokens used per GenAI request, split by token type.",
)

_operation_duration = _meter.create_histogram(
    "gen_ai.client.operation.duration",
    unit="s",
    description="GenAI operation wall-clock duration.",
)

_requests = _meter.create_counter(
    "rag.requests",
    unit="{request}",
    description="Q&A requests by query type and outcome.",
)

_citation_invalid = _meter.create_counter(
    "rag.citations.invalid",
    unit="{marker}",
    description="Invalid citation markers detected before answer release.",
)

_citation_unbound = _meter.create_counter(
    "rag.citations.unbound",
    unit="{marker}",
    description="Citation markers without an emitted source binding.",
)

_citation_missing_binding = _meter.create_counter(
    "rag.citations.missing_binding",
    unit="{answer}",
    description="Material document answers with no citation binding.",
)

_citation_repairs = _meter.create_counter(
    "rag.citations.repair",
    unit="{answer}",
    description="Bounded citation repair attempts.",
)

_follow_up_suggestion_decisions = _meter.create_counter(
    "rag.follow_up_suggestions",
    unit="{answer}",
    description="Deterministic terminal follow-up decisions by bounded mode.",
)

_follow_up_suggestion_duration = _meter.create_histogram(
    "rag.follow_up_suggestions.duration",
    unit="s",
    description="Deterministic terminal follow-up decision latency.",
)

_legacy_filter_normalizations = _meter.create_counter(
    "rag.record_filter.legacy_normalizations",
    unit="{clause}",
    description="Legacy record-filter clauses normalized at the public schema boundary.",
)

_render_guard_trips = _meter.create_counter(
    "bq.render_guard.trips",
    unit="{trip}",
    description="Rich-render value-preservation guard trips (fallback to present_answered).",
)

_business_query_failures = _meter.create_counter(
    "bq.failures",
    unit="{failure}",
    description="Business Query failures by bounded failure kind.",
)


def record_token_usage(
    *, operation: str, model: str, input_tokens: int, output_tokens: int
) -> None:
    """Record one data point per token type (the semconv shape)."""
    attrs = {"gen_ai.operation.name": operation, "gen_ai.request.model": model}
    _token_usage.record(input_tokens, {**attrs, "gen_ai.token.type": "input"})
    _token_usage.record(output_tokens, {**attrs, "gen_ai.token.type": "output"})


def record_operation_duration(*, operation: str, model: str, seconds: float) -> None:
    _operation_duration.record(
        seconds,
        {"gen_ai.operation.name": operation, "gen_ai.request.model": model},
    )


def record_request(*, query_type: str, outcome: str) -> None:
    _requests.add(1, {"query_type": query_type, "outcome": outcome})


def record_legacy_filter_normalization(*, clauses: int) -> None:
    """Count transition use with no caller- or value-derived attributes."""
    if clauses > 0:
        _legacy_filter_normalizations.add(clauses)


def record_render_guard_trip() -> None:
    """Emit observable telemetry when the rich-render value guard trips."""
    _render_guard_trips.add(1)
    _logger.info("render_guard_trip")


def record_business_query_failure(*, kind: str) -> None:
    """Count a bounded Business Query failure kind.

    ``kind`` is deliberately an allow-list rather than a free-form exception
    or database message.  Failure details belong on a sampled span/log; this
    metric is for stable alerting dimensions only.
    """
    allowed = {
        "schema",
        "query_record",
        "execution_event",
        "planner",
        "scope",
        "sql",
        "detail",
        "presenter",
        "transport",
    }
    if kind not in allowed:
        raise ValueError(f"unsupported Business Query failure kind: {kind!r}")
    _business_query_failures.add(1, {"failure.kind": kind})


def record_citation_finalization(
    *, invalid_markers: int, unbound_markers: int, missing_bindings: int, repair_attempted: bool
) -> None:
    """Record low-cardinality citation-finalization outcomes."""
    if invalid_markers:
        _citation_invalid.add(invalid_markers)
    if unbound_markers:
        _citation_unbound.add(unbound_markers)
    if missing_bindings:
        _citation_missing_binding.add(missing_bindings)
    if repair_attempted:
        _citation_repairs.add(1)


def record_follow_up_suggestion_decision(*, mode: str, seconds: float) -> None:
    """Record the bounded suggestion mode and local decision duration."""
    attributes = {"mode": mode}
    _follow_up_suggestion_decisions.add(1, attributes)
    _follow_up_suggestion_duration.record(seconds, attributes)


def circuit_breaker_observations(_options) -> list:
    """Polled by the metric reader: 1 per open breaker, 0 otherwise."""
    from opentelemetry.metrics import Observation

    from app.core.breakers import ALL_BREAKERS, BreakerState

    return [
        Observation(1 if b.state is BreakerState.OPEN else 0, {"circuit_breaker.name": b.name})
        for b in ALL_BREAKERS
    ]


def register_circuit_breaker_gauge(callback: Callable) -> None:
    """Register an observable gauge for circuit-breaker state (1=open, else 0).

    ``callback`` is an OTel observable-gauge callback returning Observations;
    it is polled by the metric reader at export time, so it always reflects
    current breaker state without per-event recording.
    """
    _meter.create_observable_gauge(
        "circuit_breaker.open",
        callbacks=[callback],
        unit="{state}",
        description="1 when a circuit breaker is open, else 0.",
    )
