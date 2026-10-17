"""

OpenTelemetry instrumentation boundary.

Pipelines and HTTP entry points import from here:

    from app.telemetry import setup_telemetry, instrument_retriever, instrument_llm

"""

from app.telemetry.context import run_in_thread
from app.telemetry.runnable import instrument_llm, instrument_retriever
from app.telemetry.setup import setup_telemetry, shutdown_telemetry
from app.telemetry.spans import (
    access_control_span,
    classify_span,
    emit_circuit_breaker_reject,
    guardrails_redact_span,
    guardrails_sql_anonymize_span,
    llm_span,
    retriever_span,
)

__all__ = [
    "access_control_span",
    "classify_span",
    "emit_circuit_breaker_reject",
    "guardrails_redact_span",
    "guardrails_sql_anonymize_span",
    "instrument_llm",
    "instrument_retriever",
    "llm_span",
    "retriever_span",
    "run_in_thread",
    "setup_telemetry",
    "shutdown_telemetry",
]
