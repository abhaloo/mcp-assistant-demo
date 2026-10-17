"""OpenTelemetry TracerProvider and FastAPI instrumentation."""

from __future__ import annotations

import logging
import socket
from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter

from app.config import settings
from app.telemetry.correlation import CorrelationSpanProcessor
from app.telemetry.langsmith_capture import build_capture_client
from app.telemetry.logging_setup import configure_logging
from app.telemetry.metrics import GENAI_TOKEN_BUCKETS

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_provider_configured = False
_instrumented_app_ids: set[int] = set()
_circuit_breaker_gauge_registered = False
_shutdown_provider_ids: set[int] = set()


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": settings.telemetry.service_name,
            "service.version": settings.api_version,
            "deployment.environment": settings.telemetry.environment,
            "service.instance.id": socket.gethostname(),
        }
    )


def _console_exporter() -> SpanExporter:
    return ConsoleSpanExporter()


def _otlp_exporter() -> SpanExporter:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=settings.telemetry.otlp_endpoint)


def _console_metric_exporter() -> MetricExporter:
    return ConsoleMetricExporter()


def _otlp_metric_exporter() -> MetricExporter:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

    return OTLPMetricExporter(endpoint=settings.telemetry.otlp_endpoint)


def _configure_meter_provider() -> None:
    """Push-based metrics via PeriodicExportingMetricReader (default 60s)."""
    exporter: MetricExporter
    if settings.telemetry.exporter == "console":
        exporter = _console_metric_exporter()
    elif settings.telemetry.exporter == "otlp":
        exporter = _otlp_metric_exporter()
    else:
        raise ValueError(f"Unknown telemetry_exporter: {settings.telemetry.exporter!r}")

    reader = PeriodicExportingMetricReader(exporter)
    token_view = View(
        instrument_name="gen_ai.client.token.usage",
        aggregation=ExplicitBucketHistogramAggregation(GENAI_TOKEN_BUCKETS),
    )
    provider = MeterProvider(
        resource=_build_resource(),
        metric_readers=[reader],
        views=[token_view],
    )
    metrics.set_meter_provider(provider)


def _configure_azure_monitor() -> None:
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
    except ImportError as exc:
        raise RuntimeError(
            "telemetry_exporter=azure_monitor requires azure-monitor-opentelemetry. "
            'Install with: pip install -e ".[azure]"'
        ) from exc

    if not settings.applicationinsights_connection_string:
        raise ValueError(
            "APPLICATIONINSIGHTS_CONNECTION_STRING is required when "
            "telemetry_exporter=azure_monitor"
        )

    configure_azure_monitor(
        connection_string=settings.applicationinsights_connection_string,
        resource=_build_resource(),
    )


def _register_circuit_breaker_gauge() -> None:
    """Register observable gauge for circuit-breaker open state."""
    global _circuit_breaker_gauge_registered
    if _circuit_breaker_gauge_registered:
        return
    try:
        from app.telemetry.metrics import (
            circuit_breaker_observations,
            register_circuit_breaker_gauge,
        )

        register_circuit_breaker_gauge(circuit_breaker_observations)
        _circuit_breaker_gauge_registered = True
    except Exception:
        logger.warning("Failed to register circuit breaker gauge", exc_info=True)


def _configure_tracer_provider() -> None:
    global _provider_configured
    if _provider_configured:
        return

    # A test or an embedding host can replace the SDK provider between app
    # lifecycles. The idempotence ledger only belongs to the previous provider
    # generation; clear it before installing a fresh generation.
    _shutdown_provider_ids.clear()

    if settings.telemetry.exporter == "azure_monitor":
        try:
            _configure_azure_monitor()
        except Exception:
            # An observability exporter must never crash the service. If Azure Monitor
            # setup fails (e.g. an azure-monitor / otel-sdk version skew), log it and fall
            # back to a bare provider so the app still boots and trace context still flows.
            logger.warning(
                "Azure Monitor telemetry setup failed; continuing without App Insights export.",
                exc_info=True,
            )
            trace.set_tracer_provider(TracerProvider(resource=_build_resource()))
        _instrument_asyncio()
        _register_circuit_breaker_gauge()
        _provider_configured = True
        return

    exporter: SpanExporter
    if settings.telemetry.exporter == "console":
        exporter = _console_exporter()
    elif settings.telemetry.exporter == "otlp":
        exporter = _otlp_exporter()
    else:
        raise ValueError(f"Unknown telemetry_exporter: {settings.telemetry.exporter!r}")

    provider = TracerProvider(resource=_build_resource())
    provider.add_span_processor(CorrelationSpanProcessor())
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _instrument_asyncio()
    _configure_meter_provider()
    _register_circuit_breaker_gauge()
    _provider_configured = True


def _instrument_asyncio() -> None:
    """Ensure asyncio task/to_thread boundaries preserve trace context."""
    try:
        from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
    except ImportError:
        return
    AsyncioInstrumentor().instrument()


def instrument_fastapi(app: FastAPI) -> None:
    """Attach FastAPI auto-instrumentation to a single app instance."""
    app_id = id(app)
    if app_id in _instrumented_app_ids:
        return

    _configure_tracer_provider()
    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="/healthz,/readyz",
    )
    _instrumented_app_ids.add(app_id)


def setup_telemetry(app: FastAPI | None = None) -> None:
    """Configure OTel exporters and optionally instrument a FastAPI app."""
    configure_logging()
    # Build the PII-scrubbing LangSmith capture client once at startup.
    # Side-effect only: returns (and caches) None when tracing is off; raises
    # if tracing is on without a key. ask_service reads it via get_capture_client().
    build_capture_client()
    _configure_tracer_provider()
    if app is not None:
        instrument_fastapi(app)


def shutdown_telemetry() -> None:
    """Flush and shut down the providers so buffered spans/metrics aren't lost."""
    global _provider_configured

    for provider in (trace.get_tracer_provider(), metrics.get_meter_provider()):
        provider_id = id(provider)
        if provider_id in _shutdown_provider_ids:
            continue
        if hasattr(provider, "force_flush"):
            provider.force_flush()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
        _shutdown_provider_ids.add(provider_id)

    _provider_configured = False
