"""Manual OpenTelemetry spans for RAG pipeline stages."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span

from app.config import settings
from app.telemetry.correlation import current_correlation_id
from app.telemetry.helpers import set_gen_ai_attributes
from app.telemetry.metrics import record_operation_duration

tracer = trace.get_tracer("app.telemetry")


def _redaction_state() -> str:
    return "enabled" if settings.redaction_enabled else "disabled"


@contextmanager
def _pipeline_span(
    name: str,
    *,
    set_attrs: Callable[[Span], None] | None = None,
    track_duration: tuple[str, str] | None = None,
) -> Iterator[Span]:
    """Shared span factory — optional attrs hook and GenAI duration recording."""
    start = time.perf_counter() if track_duration else None
    with tracer.start_as_current_span(name) as span:
        if set_attrs is not None:
            set_attrs(span)
        try:
            yield span
        finally:
            if track_duration is not None:
                operation, model = track_duration
                record_operation_duration(
                    operation=operation,
                    model=model,
                    seconds=time.perf_counter() - start,  # type: ignore[arg-type]
                )


@contextmanager
def business_query_stage_span(
    stage: str,
    *,
    correlation_id: str | None = None,
    answer_query_id: str | None = None,
    outcome: str | None = None,
) -> Iterator[Span]:
    """Trace one Business Query stage with bounded, joinable metadata.

    Correlation and Answer Query IDs are span attributes (not metric labels),
    so support can join a trace without creating unbounded metric series.
    ``stage`` is constrained to the stable pipeline vocabulary used by the
    alerting metric and transport contract.
    """
    correlation_id = correlation_id or current_correlation_id()
    allowed = {
        "planner",
        "scope",
        "sql",
        "detail",
        "presenter",
        "terminal_persistence",
        "transport",
    }
    if stage not in allowed:
        raise ValueError(f"unsupported Business Query stage: {stage!r}")

    def _attrs(span: Span) -> None:
        span.set_attribute("bq.stage", stage)
        if correlation_id:
            span.set_attribute("correlation_id", correlation_id)
        if answer_query_id:
            span.set_attribute("answer_query_id", answer_query_id)
        if outcome:
            span.set_attribute("bq.outcome", outcome)

    with _pipeline_span(f"business_query.{stage}", set_attrs=_attrs) as span:
        yield span


@contextmanager
def planner_stage_span(**ids: str | None) -> Iterator[Span]:
    with business_query_stage_span("planner", **ids) as span:
        yield span


@contextmanager
def scope_stage_span(**ids: str | None) -> Iterator[Span]:
    with business_query_stage_span("scope", **ids) as span:
        yield span


@contextmanager
def sql_stage_span(**ids: str | None) -> Iterator[Span]:
    with business_query_stage_span("sql", **ids) as span:
        yield span


@contextmanager
def detail_stage_span(**ids: str | None) -> Iterator[Span]:
    with business_query_stage_span("detail", **ids) as span:
        yield span


@contextmanager
def presenter_stage_span(**ids: str | None) -> Iterator[Span]:
    with business_query_stage_span("presenter", **ids) as span:
        yield span


@contextmanager
def terminal_persistence_stage_span(**ids: str | None) -> Iterator[Span]:
    with business_query_stage_span("terminal_persistence", **ids) as span:
        yield span


@contextmanager
def transport_stage_span(**ids: str | None) -> Iterator[Span]:
    with business_query_stage_span("transport", **ids) as span:
        yield span


@contextmanager
def retriever_span(
    *,
    k: int | None = None,
    branch: str = "context",
    access_tiers: list[str] | None = None,
) -> Iterator[Span]:
    """Span around document retrieval."""

    def _attrs(span: Span) -> None:
        span.set_attribute("retriever_kind", settings.retriever_kind)
        span.set_attribute("k", k if k is not None else settings.top_k)
        span.set_attribute("retriever.branch", branch)
        span.set_attribute("redaction_state", _redaction_state())
        if access_tiers is not None:
            span.set_attribute("retrieval.access_tiers", ",".join(sorted(access_tiers)))

    with _pipeline_span("rag.retriever", set_attrs=_attrs) as span:
        yield span


@contextmanager
def llm_span(*, operation: str = "generation") -> Iterator[Span]:
    """Span around LLM generation — follows GenAI semconv (no prompt content)."""
    span_name = f"{operation} {settings.active_chat_model}"

    def _attrs(span: Span) -> None:
        set_gen_ai_attributes(span, operation=operation)
        span.set_attribute("redaction_state", _redaction_state())

    with _pipeline_span(
        span_name,
        set_attrs=_attrs,
        track_duration=(operation, settings.active_chat_model),
    ) as span:
        yield span


@contextmanager
def access_control_span(*, role: str, permissions: list[str]) -> Iterator[Span]:
    """Span around tier resolution — role + permissions for alerting."""

    def _attrs(span: Span) -> None:
        span.set_attribute("access.role", role)
        span.set_attribute("access.permissions", ",".join(sorted(permissions)))

    with _pipeline_span("access_control.resolve_tiers", set_attrs=_attrs) as span:
        yield span


@contextmanager
def classify_span() -> Iterator[Span]:
    """Span around query routing — set query_type before exit."""
    with _pipeline_span("router.classify") as span:
        yield span


@contextmanager
def guardrails_redact_span() -> Iterator[Span]:
    """Span around document PII redaction (RAG path)."""

    def _attrs(span: Span) -> None:
        span.set_attribute("guardrails.enabled", settings.redaction_enabled)
        span.set_attribute("redaction_state", _redaction_state())

    with _pipeline_span("guardrails.redact_documents", set_attrs=_attrs) as span:
        yield span


@contextmanager
def guardrails_sql_anonymize_span() -> Iterator[Span]:
    """Span around SQL-path PII tokenization."""

    def _attrs(span: Span) -> None:
        span.set_attribute("guardrails.enabled", True)
        span.set_attribute("redaction_state", _redaction_state())

    with _pipeline_span("guardrails.anonymize_sql", set_attrs=_attrs) as span:
        yield span


def emit_circuit_breaker_reject(*, breaker_name: str, state: str) -> None:
    """Emit a zero-duration span when a circuit breaker rejects a call."""
    with tracer.start_as_current_span("circuit_breaker.reject") as span:
        span.set_attribute("circuit_breaker.name", breaker_name)
        span.set_attribute("circuit_breaker.state", state)
        span.set_attribute("circuit_breaker.rejected", True)
