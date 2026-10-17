"""Terminal-state snapshot for Query Record writes."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic

from app.auth import Principal
from app.services.run_lifecycle import RunOutcome


@dataclass
class TerminalSnapshot:
    """Captured once at terminal state — immutable input to the builder."""

    correlation_id: str
    question: str
    principal: Principal
    terminal_outcome: str
    run_outcome: RunOutcome
    query_type: str | None = None
    requested_route: str | None = None
    effective_route: str | None = None
    prompt_version: str | None = None
    context_mode: str = "none"
    records_only: bool = False
    cancelled: bool = False
    started_at: float = field(default_factory=monotonic)
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    estimated_usd: Decimal | None = None
    cost_status: str | None = None
    model: str | None = None
    provider: str | None = None
    resolver_query_id: str | None = None
    resolver_disposition: str | None = None
    # BQ trace block: JSON-encoded SQL timing, executor Answer Query ID, and
    # bounded result shape. None for semantic turns.
    bq_trace_json: str | None = None
    planner_ms: float | None = None
    sql_ms: float | None = None
    latency_ms: int | None = None
    first_activity_ms: int | None = None
    first_row_ms: int | None = None
    completion_ms: int | None = None
    deadline_remaining_ms: int | None = None
    repair_count: int | None = None
    rows_returned: int | None = None
    failure_layer: str | None = None
    trace_completeness: str | None = None
    stable_error_code: str | None = None
    timeout: bool | None = None
    # What the turn showed the person (a turn_content.TurnContent), or None for a
    # transport that hands none. Typed as object: app.telemetry.invocation_ledger
    # imports this module, and the import-cycle gate counts type-only imports.
    turn_content: object | None = None

    @property
    def first_progress_event_ms(self) -> int | None:
        return self.first_activity_ms

    @first_progress_event_ms.setter
    def first_progress_event_ms(self, value: int | None) -> None:
        self.first_activity_ms = value

    def completion_latency_ms(self) -> int:
        if self.completion_ms is not None:
            return self.completion_ms
        elapsed = monotonic() - self.started_at
        return max(0, int(elapsed * 1000))


@dataclass
class TerminalUsageCapture:
    """Mutable holder filled by stream producers for terminal Query Record write."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_input_tokens: int | None = None
    estimated_usd: Decimal | None = None
    cost_status: str | None = None
    model: str | None = None
    provider: str | None = None
    bq_trace_json: str | None = None
    resolver_disposition: str | None = None

    def take_usage(self, other: TerminalUsageCapture) -> None:
        """Copy the turn's priced spend, cost status, and token counts when it
        has any. Copy model and provider when the other capture named a call
        that ran."""
        if other.input_tokens is not None or other.output_tokens is not None:
            self.input_tokens = other.input_tokens
            self.output_tokens = other.output_tokens
            self.reasoning_tokens = other.reasoning_tokens
            self.cached_input_tokens = other.cached_input_tokens
        self.estimated_usd = other.estimated_usd
        self.cost_status = other.cost_status
        if other.model is not None:
            self.model = other.model
            self.provider = other.provider

    def model_for_record(self) -> str | None:
        """The model a call in this turn actually used. Never a config default."""
        return self.model

    def provider_for_record(self) -> str | None:
        """The provider of the call that named the model. Never a config default."""
        return self.provider if self.model is not None else None
