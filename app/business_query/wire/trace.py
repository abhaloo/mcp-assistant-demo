"""Out-of-band observability for one business query.

Deliberately NOT part of the outcome union. ADR 0047 keeps SQL, table names and
backend detail out of everything the caller can see, so the module cannot answer
"how long did the SQL take" or "which member was missing" through its return
value. A caller that is entitled to know — the eval harness — passes a trace in;
the planner and adapter only ever write to it.

Timing is split by component for the same reason the legacy SQL suite splits it:
a slow planner and a slow query need different fixes, and one total hides both.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field, fields
from typing import Any

# Keep SQL STRUCTURE, mask literal VALUES. `WHERE name = 'Ali Hassan'` must never
# reach a run file, and that guarantee cannot depend on a PII detector — same rule
# the legacy suite applies before persisting generated SQL.
_STRING_LITERAL = re.compile(r"'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\"")


def mask_sql_literals(sql: str | None) -> str | None:
    return _STRING_LITERAL.sub("?", sql) if sql else sql


_PERIOD_BOUND_KEYS = ("on", "since", "between")
_CLARIFY_TEXT_KEYS = ("question", "clarify", "clarification_question")


def mask_plan_values(plan: dict) -> dict:
    """Keep member names and operators; redact filter values and period bounds."""
    masked = copy.deepcopy(plan)
    _redact_filter_values(masked.get("filters"))
    _redact_filter_values(masked.get("having"))
    period = masked.get("period")
    if isinstance(period, dict):
        for key in _PERIOD_BOUND_KEYS:
            if period.get(key) is not None:
                period[key] = _redact_leaf(period[key])
    return masked


def mask_planner_payload(payload: dict) -> dict:
    """Envelope-wide masking: filter values (any depth), period bounds (under
    any dict keyed ``period``), and clarification free text.

    Malformed payloads mis-nest, so the value walk covers the whole dict rather
    than trusting the ``plan`` key. ``question``, ``clarify``, and
    ``clarification_question`` are redacted at every depth. Member names,
    operators, and structure survive; anything that can echo user-typed text
    does not.
    """
    masked = copy.deepcopy(payload)
    _redact_filter_values(masked)
    _redact_period_bounds(masked)
    _redact_clarify_text(masked)
    return masked


def _redact_clarify_text(node: Any) -> None:
    if isinstance(node, dict):
        for key in _CLARIFY_TEXT_KEYS:
            if node.get(key) is not None:
                node[key] = _redact_leaf(node[key])
        for child in node.values():
            _redact_clarify_text(child)
    elif isinstance(node, list):
        for child in node:
            _redact_clarify_text(child)


def _redact_period_bounds(node: Any) -> None:
    if isinstance(node, dict):
        period = node.get("period")
        if isinstance(period, dict):
            for key in _PERIOD_BOUND_KEYS:
                if period.get(key) is not None:
                    period[key] = _redact_leaf(period[key])
        for child in node.values():
            _redact_period_bounds(child)
    elif isinstance(node, list):
        for child in node:
            _redact_period_bounds(child)


def _redact_leaf(value: Any) -> Any:
    if isinstance(value, list):
        return ["?" for _ in value]
    if isinstance(value, tuple):
        return tuple("?" for _ in value)
    return "?"


def _redact_filter_values(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("values") is not None:
            node["values"] = _redact_leaf(node["values"])
        if node.get("value") is not None:
            node["value"] = "?"
        for child in node.values():
            _redact_filter_values(child)
    elif isinstance(node, list):
        for child in node:
            _redact_filter_values(child)


@dataclass
class SubQuerySnapshot:
    """Sealed identity and SQL for one ordinal inside a turn-scoped trace."""

    answer_query_id: str | None = None
    plan_fingerprint: str | None = None
    resolver_query_id: str | None = None
    rows_returned: int | None = None
    sql_statement_full: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer_query_id": self.answer_query_id,
            "plan_fingerprint": self.plan_fingerprint,
            "resolver_query_id": self.resolver_query_id,
            "rows_returned": self.rows_returned,
        }

    def as_ledger_dict(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload["sql_statement_full"] = self.sql_statement_full
        return payload


@dataclass
class QueryTrace:
    """What happened inside one query, for a caller allowed to see it."""

    correlation_id: str | None = None
    classifier_ms: float | None = None
    conversation_ms: float | None = None
    planner_ms: float | None = None  # cumulative across repair rounds
    planner_repair_count: int = 0
    sql_ms: float | None = None
    detail_read_ms: float | None = None
    rich_render_ms: float | None = None
    finalization_ms: float | None = None
    first_progress_ms: float | None = None  # renamed from legacy ttft_ms
    first_activity_ms: float | None = None
    first_row_ms: float | None = None
    deadline_remaining_ms: float | None = None
    completion_ms: float | None = None
    latency_ms: float | None = None
    total_ms: float | None = None
    route: str | None = None
    provider: str | None = None
    deployment: str | None = None
    answer_query_id: str | None = None
    terminal_reason: str | None = None
    sql: str | None = None
    # Full statement WITH literal values (D-U6) -- feeds the owned-Postgres SQL
    # ledger and the turn-record trace block ONLY. Deliberately excluded from
    # `as_dict()` (below) so it never reaches the eval JSONL artifact, which
    # stays byte-identical to the pre-D-U6 masked shape.
    sql_statement_full: str | None = None
    rows_returned: int | None = None
    total_row_count: int | None = None
    truncated: bool | None = None
    completeness: str | None = None
    continuation_token: str | None = None
    reasoning: str | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    tokens_reasoning: int | None = None
    capture_planner_payload: bool = False
    planner_raw_payload: str | None = None
    failure_layer: str | None = None
    failure_detail: str | None = None
    missing_members: list[str] = field(default_factory=list)
    plan: dict | None = None
    grain_check_site: str | None = None
    provider_http_retry_budget: int | None = None
    resolver_query_id: str | None = None
    resolver_started: bool = False
    resolver_disposition: str | None = None
    resolver_match_count: int | None = None
    resolver_version: str | None = None
    resolver_value_type: str | None = None
    sub_queries: list[SubQuerySnapshot] = field(default_factory=list)

    @property
    def first_progress_event_ms(self) -> float | None:
        return (
            self.first_activity_ms if self.first_activity_ms is not None else self.first_progress_ms
        )

    @first_progress_event_ms.setter
    def first_progress_event_ms(self, value: float | None) -> None:
        self.first_activity_ms = value
        self.first_progress_ms = value

    def record_rich_render(self, elapsed_ms: float) -> None:
        self.rich_render_ms = elapsed_ms

    def record_first_progress(self, elapsed_ms: float) -> None:
        self.first_progress_ms = elapsed_ms
        if self.first_activity_ms is None:
            self.first_activity_ms = elapsed_ms

    def record_first_activity(self, elapsed_ms: float) -> None:
        self.first_activity_ms = elapsed_ms
        self.first_progress_ms = elapsed_ms

    def record_first_row(self, elapsed_ms: float) -> None:
        self.first_row_ms = elapsed_ms

    def record_deadline_remaining(self, remaining_ms: float) -> None:
        self.deadline_remaining_ms = remaining_ms

    def record_completion(self, elapsed_ms: float) -> None:
        self.completion_ms = elapsed_ms
        self.total_ms = elapsed_ms

    def reconcile_total_ms(self) -> float:
        """Calculate the sum of all recorded stage durations."""
        stages = [
            self.classifier_ms,
            self.conversation_ms,
            self.planner_ms,
            self.sql_ms,
            self.detail_read_ms,
            self.rich_render_ms,
            self.finalization_ms,
        ]
        return sum(s for s in stages if s is not None)

    def to_timing_receipt(self) -> Any:
        from app.business_query.outcomes import TimingReceipt

        return TimingReceipt(
            classifier_ms=self.classifier_ms,
            conversation_ms=self.conversation_ms,
            planner_ms=self.planner_ms,
            sql_ms=self.sql_ms,
            detail_read_ms=self.detail_read_ms,
            rich_render_ms=self.rich_render_ms,
            finalization_ms=self.finalization_ms,
            first_progress_ms=self.first_progress_ms,
            total_ms=self.total_ms,
            route=self.route,
            provider=self.provider,
            deployment=self.deployment,
            answer_query_id=self.answer_query_id,
            terminal_reason=self.terminal_reason,
        )

    def record_sql(
        self,
        statement: str,
        *,
        elapsed_ms: float,
        rows: int | None,
        full_statement: str | None = None,
    ) -> None:
        self.sql = mask_sql_literals(statement)
        self.sql_ms = elapsed_ms
        self.rows_returned = rows
        self.sql_statement_full = full_statement

    def append_sealed_sub_query(
        self,
        local: QueryTrace,
        *,
        answer_query_id: str | None,
        plan_fingerprint: str | None,
        rows_returned: int | None = None,
    ) -> None:
        """Keep one sealed ordinal on the parent after that ordinal finishes."""
        sealed_rows = local.rows_returned if local.rows_returned is not None else rows_returned
        self.sub_queries.append(
            SubQuerySnapshot(
                answer_query_id=answer_query_id,
                plan_fingerprint=plan_fingerprint,
                resolver_query_id=local.resolver_query_id,
                rows_returned=sealed_rows,
                sql_statement_full=local.sql_statement_full,
            )
        )
        if self.sql is None and local.sql is not None:
            self.sql = local.sql
            self.sql_statement_full = local.sql_statement_full
            self.sql_ms = local.sql_ms
            self.rows_returned = sealed_rows
        if self.resolver_query_id is None and local.resolver_query_id is not None:
            self.resolver_query_id = local.resolver_query_id

    def record_plan(self, plan: dict) -> None:
        self.plan = mask_plan_values(plan)

    def fail(
        self,
        layer: str,
        detail: str,
        *,
        members: list[str] | None = None,
        grain_check_site: str | None = None,
    ) -> None:
        self.failure_layer = layer
        self.failure_detail = detail
        if members:
            self.missing_members = sorted(members)
        if grain_check_site is not None:
            self.grain_check_site = grain_check_site

    def copy_into(self, dest: QueryTrace) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "missing_members":
                value = list(value)
            elif item.name == "plan" and value is not None:
                value = copy.deepcopy(value)
            elif item.name == "sub_queries":
                value = copy.deepcopy(value)
            setattr(dest, item.name, value)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for item in fields(self):
            # Full-fidelity SQL (parent and per-ordinal) stays on the in-process
            # object for the owned-Postgres ledger. Eval JSONL never receives it.
            if item.name == "sql_statement_full":
                continue
            value = getattr(self, item.name)
            if item.name == "missing_members":
                value = list(value)
            elif item.name == "plan" and value is not None:
                value = copy.deepcopy(value)
            elif item.name == "sub_queries":
                value = [snap.as_dict() for snap in value]
            payload[item.name] = value
        if (
            payload.get("first_activity_ms") is None
            and payload.get("first_progress_ms") is not None
        ):
            payload["first_activity_ms"] = payload["first_progress_ms"]
        if (
            payload.get("first_progress_ms") is None
            and payload.get("first_activity_ms") is not None
        ):
            payload["first_progress_ms"] = payload["first_activity_ms"]
        return payload
