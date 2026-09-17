"""Helpers to bridge BQ usage into terminal query-record capture."""

from __future__ import annotations

from app.pricing.pricer import price_tokens
from app.query_records.context import TerminalUsageCapture
from app.services.business_query_service import AskBusinessQueryResult


def fill_terminal_usage_from_bq(capture: TerminalUsageCapture, bq: AskBusinessQueryResult) -> None:
    """Exactly-once terminal usage fill for BQ SSE turns."""
    capture.input_tokens = bq.input_tokens
    capture.output_tokens = bq.output_tokens
    capture.reasoning_tokens = bq.reasoning_tokens
    capture.model = bq.model
    capture.bq_trace_json = bq.bq_trace_json
    capture.resolver_disposition = bq.disposition
    priced = price_tokens(
        model=bq.model,
        input_tokens=bq.input_tokens,
        output_tokens=bq.output_tokens,
        reasoning_tokens=bq.reasoning_tokens,
    )
    capture.cost_status = priced.cost_status
