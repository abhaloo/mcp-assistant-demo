"""Typed Query Record payloads (repository boundary)."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SortDirection = Literal["asc", "desc"]


class QueryRecordData(BaseModel):
    """Insert payload — every field optional except identity keys."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str
    project_id: str
    environment: str
    created_at: datetime | None = None
    retention_at: datetime | None = None

    redacted_question: str | None = None
    question_fingerprint: str | None = None
    raw_question: str | None = None

    billing_commit: str | None = None
    rag_commit: str | None = None
    service_versions: str | None = None

    manifest_hash: str | None = None
    prompt_versions: str | None = None
    price_table_version: str | None = None

    record_dispatch_mode: str | None = None
    filter_mode: str | None = None
    analytics_mode: str | None = None
    conversation_mode: str | None = None
    citation_mode: str | None = None
    thread_id: str | None = None

    subject_digest: str | None = None
    entity_digest: str | None = None
    role_class: str | None = None
    context_mode: str | None = None

    requested_route: str | None = None
    effective_route: str | None = None
    route_reason: str | None = None
    fallback_flag: bool | None = None

    ui_first_text_ms: int | None = None
    completion_latency_ms: int | None = None
    terminal_outcome: str | None = None

    model: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    estimated_usd: Decimal | None = None
    cost_status: str | None = None

    retrieved_count: int | None = None
    used_source_count: int | None = None
    citation_count: int | None = None
    invalid_citation_count: int | None = None
    unbound_citation_count: int | None = None
    missing_binding_count: int | None = None
    repair_attempted: bool | None = None
    finalization_outcome: str | None = None

    record_outcome: str | None = None
    sql_present: bool | None = None
    sanitized_sql: str | None = None
    sql_fingerprint: str | None = None
    projected_views: str | None = None
    operation_class: str | None = None
    policy_verdict: str | None = None
    row_count: int | None = None

    retry_count: int | None = None
    cancelled: bool | None = None
    timeout: bool | None = None
    stable_error_code: str | None = None

    feedback_verdict: str | None = None
    feedback_at: datetime | None = None
    frozen_case_id: str | None = None
    evaluation_result: str | None = None
    evaluation_at: datetime | None = None
    resolver_query_id: str | None = None
    resolver_disposition: str | None = None
    # BQ trace block: JSON-encoded, full-fidelity SQL.
    bq_trace_json: str | None = None
    # Plan persistence
    plan_payload: str | None = None
    plan_expires_at: datetime | None = None

    @property
    def planner_ms(self) -> float | None:
        data = self._parsed_bq_trace()
        if data and "planner_ms" in data and data["planner_ms"] is not None:
            return float(data["planner_ms"])
        return None

    @property
    def sql_ms(self) -> float | None:
        data = self._parsed_bq_trace()
        if data and "sql_ms" in data and data["sql_ms"] is not None:
            return float(data["sql_ms"])
        return None

    @property
    def latency_ms(self) -> int | None:
        data = self._parsed_bq_trace()
        if data and "latency_ms" in data and data["latency_ms"] is not None:
            return int(data["latency_ms"])
        return self.completion_latency_ms

    @property
    def first_activity_ms(self) -> int | None:
        data = self._parsed_bq_trace()
        if data:
            val = (
                data.get("first_activity_ms")
                or data.get("first_progress_event_ms")
                or data.get("first_progress_ms")
            )
            return int(val) if val is not None else None
        return self.ui_first_text_ms

    @property
    def first_progress_event_ms(self) -> int | None:
        return self.first_activity_ms

    @property
    def first_row_ms(self) -> int | None:
        data = self._parsed_bq_trace()
        val = data.get("first_row_ms") if data else None
        return int(val) if val is not None else None

    @property
    def completion_ms(self) -> int | None:
        data = self._parsed_bq_trace()
        val = data.get("completion_ms") if data else None
        if val is not None:
            return int(val)
        return self.completion_latency_ms

    @property
    def deadline_remaining_ms(self) -> int | None:
        data = self._parsed_bq_trace()
        val = data.get("deadline_remaining_ms") if data else None
        return int(val) if val is not None else None

    @property
    def failure_layer(self) -> str | None:
        data = self._parsed_bq_trace()
        return data.get("failure_layer") if data else None

    @property
    def repair_count(self) -> int | None:
        data = self._parsed_bq_trace()
        if data:
            val = (
                data.get("planner_repair_count")
                if data.get("planner_repair_count") is not None
                else data.get("repair_count")
            )
            return int(val) if val is not None else None
        return None

    @property
    def trace_completeness(self) -> str | None:
        data = self._parsed_bq_trace()
        return (data.get("trace_completeness") or data.get("completeness")) if data else None

    def _parsed_bq_trace(self) -> dict[str, Any] | None:
        if not self.bq_trace_json:
            return None
        try:
            payload = json.loads(self.bq_trace_json)
            return payload if isinstance(payload, dict) else None
        except (TypeError, ValueError):
            return None


class FeedbackUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback_verdict: str
    feedback_at: datetime


class TimingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui_first_text_ms: int
    completion_latency_ms: int


class EvaluationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frozen_case_id: str
    evaluation_result: str
    evaluation_at: datetime


class QueryRecordReadFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    billing_commit: str | None = None
    rag_commit: str | None = None
    manifest_hash: str | None = None
    conversation_mode: str | None = None
    feedback_verdict: str | None = None
    prompt_version: str | None = None


class QueryRecordSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    direction: SortDirection = "asc"
    # Explicit NULL placement -- not inherited silently.
    nulls: Literal["first", "last"] = "last"


class QueryListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filters: QueryRecordReadFilters
    sort: QueryRecordSort
    limit: int = Field(default=100, ge=1, le=1000)


def row_to_dict(row: Any) -> dict[str, Any]:
    """Serialize a QueryRecordRow ORM instance to plain dict values."""
    data: dict[str, Any] = {}
    for column in row.__table__.columns:
        name = column.name
        if name == "id":
            continue
        data[name] = getattr(row, name)
    return data
