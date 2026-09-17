"""Terminal-state snapshot for Query Record writes."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    cost_status: str | None = None
    model: str | None = None
    provider: str | None = None
    bq_trace_json: str | None = None
    resolver_disposition: str | None = None
