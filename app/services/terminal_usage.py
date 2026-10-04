"""Helpers to bridge BQ usage into terminal query-record capture."""

from __future__ import annotations

from app.pricing.pricer import price_tokens
from app.query_records.context import TerminalUsageCapture
from app.services.business_query_service import AskBusinessQueryResult


def fill_terminal_usage_from_bq(capture: TerminalUsageCapture, bq: AskBusinessQueryResult) -> None:
    """Seed the capture with the business tool's own call: its tokens, route
    model, trace, disposition and that call's price."""
    capture.input_tokens = bq.input_tokens
    capture.output_tokens = bq.output_tokens
    capture.reasoning_tokens = bq.reasoning_tokens
    capture.model = bq.model
    capture.provider = bq.provider if bq.model is not None else None
    capture.bq_trace_json = bq.bq_trace_json
    capture.resolver_disposition = bq.disposition
    priced = price_tokens(
        model=bq.model,
        input_tokens=bq.input_tokens,
        output_tokens=bq.output_tokens,
        reasoning_tokens=bq.reasoning_tokens,
    )
    capture.estimated_usd = priced.estimated_usd
    capture.cost_status = priced.cost_status


def fill_terminal_usage(
    capture: TerminalUsageCapture,
    *,
    bq: AskBusinessQueryResult | None,
    turn_usage: TerminalUsageCapture | None,
) -> None:
    """The business tool's own count seeds the capture with its route model and
    trace; the turn's ledger evidence, when the turn has any, outranks that count."""
    if bq is not None:
        fill_terminal_usage_from_bq(capture, bq)
    if turn_usage is not None:
        capture.take_usage(turn_usage)


def terminal_usage_from_bq(bq: AskBusinessQueryResult) -> TerminalUsageCapture:
    """Build a TerminalUsageCapture populated from a business query result."""
    capture = TerminalUsageCapture()
    fill_terminal_usage_from_bq(capture, bq)
    return capture
