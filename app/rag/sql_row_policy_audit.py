"""Audit helper for SQL row-policy accept/reject decisions (S1 / D7)."""

from __future__ import annotations

import logging

from opentelemetry import trace

logger = logging.getLogger(__name__)


def record_sql_row_policy_decision(
    *,
    accepted: bool,
    reason_code: str | None,
    table_names: list[str],
    entity_id: int | None,
) -> None:
    """Emit scrubbed accept/reject telemetry — no raw SQL, no secrets."""
    outcome = "accepted" if accepted else "rejected"
    logger.info(
        "sql_row_policy decision=%s reason_code=%s tables=%s has_entity=%s",
        outcome,
        reason_code or "-",
        ",".join(table_names) if table_names else "-",
        entity_id is not None,
    )
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("sql_row_policy.outcome", outcome)
        if reason_code:
            span.set_attribute("sql_row_policy.reason_code", reason_code)
        span.set_attribute("sql_row_policy.table_count", len(table_names))
        span.set_attribute("sql_row_policy.has_entity", entity_id is not None)
