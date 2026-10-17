"""Redacted support timeline lookup for Business Query incidents.

This module is deliberately a service seam, not a public user endpoint. A
support/admin adapter can authorize it separately and expose only this
redacted shape. It never selects questions, SQL text, model prompts, result
rows, or encrypted execution payloads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CORRELATION_ID = re.compile(r"^[0-9a-f]{32}$")
_ANSWER_QUERY_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class SupportTimelineEvent:
    source: str
    event: str
    occurred_at: datetime | None
    correlation_id: str | None = None
    answer_query_id: str | None = None
    detail_digest: str | None = None
    duration_ms: int | None = None
    outcome: str | None = None
    row_count: int | None = None
    receipt_query_id: str | None = None
    detail_family: str | None = None
    detail_owner_resource: str | None = None
    detail_owner_ref_digest: str | None = None
    detail_revision_digest: str | None = None
    detail_definition_digest: str | None = None
    detail_profile_digest: str | None = None
    detail_coverage_status: str | None = None
    detail_coverage_digest: str | None = None
    detail_provenance_digest: str | None = None


@dataclass(frozen=True)
class SupportTimeline:
    correlation_id: str | None
    answer_query_id: str | None
    events: tuple[SupportTimelineEvent, ...]


def _validate_ids(
    *, correlation_id: str | None, answer_query_id: str | None
) -> tuple[str | None, str | None]:
    if correlation_id is None and answer_query_id is None:
        raise ValueError("one of correlation_id or answer_query_id is required")
    if correlation_id is not None and not _CORRELATION_ID.fullmatch(correlation_id):
        raise ValueError("invalid correlation_id")
    if answer_query_id is not None and not _ANSWER_QUERY_ID.fullmatch(answer_query_id):
        raise ValueError("invalid answer_query_id")
    return correlation_id, answer_query_id


def _event_json_fields(raw: Any) -> tuple[int | None, str | None]:
    """Extract only bounded planner terminal fields from an event payload."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    duration = payload.get("duration_ms")
    duration_ms = duration if isinstance(duration, int) and duration >= 0 else None
    code = payload.get("terminal_code")
    outcome = code if isinstance(code, str) and len(code) <= 64 else None
    return duration_ms, outcome


class SupportTimelineLookup:
    """Read-only, redacted join across existing State/telemetry tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lookup(
        self,
        *,
        correlation_id: str | None = None,
        answer_query_id: str | None = None,
    ) -> SupportTimeline:
        correlation_id, answer_query_id = _validate_ids(
            correlation_id=correlation_id, answer_query_id=answer_query_id
        )

        if answer_query_id is not None:
            row = (
                (
                    await self._session.execute(
                        text(
                            "SELECT answer_query_id, metadata_json "
                            "FROM business_query_execution_events "
                            "WHERE answer_query_id = :answer_query_id"
                        ),
                        {"answer_query_id": answer_query_id},
                    )
                )
                .mappings()
                .first()
            )
            if row is not None and correlation_id is None:
                try:
                    metadata = json.loads(row["metadata_json"])
                except (TypeError, ValueError):
                    metadata = {}
                candidate = metadata.get("correlation_id") if isinstance(metadata, dict) else None
                if isinstance(candidate, str) and _CORRELATION_ID.fullmatch(candidate):
                    correlation_id = candidate

        events: list[SupportTimelineEvent] = []
        if correlation_id is not None:
            record = (
                (
                    await self._session.execute(
                        text(
                            "SELECT created_at, correlation_id, context_mode, "
                            "terminal_outcome, completion_latency_ms, row_count, "
                            "sql_present "
                            "FROM query_records WHERE correlation_id = :correlation_id"
                        ),
                        {"correlation_id": correlation_id},
                    )
                )
                .mappings()
                .first()
            )
            if record is not None:
                events.append(
                    SupportTimelineEvent(
                        source="query_records",
                        event="terminal",
                        occurred_at=record["created_at"],
                        correlation_id=record["correlation_id"],
                        duration_ms=record["completion_latency_ms"],
                        outcome=record["terminal_outcome"],
                        row_count=record["row_count"],
                    )
                )

            planner_rows = await self._session.execute(
                text(
                    "SELECT created_at, event_kind, event_json "
                    "FROM business_query_planner_attempt_events "
                    "WHERE case_id = :correlation_id ORDER BY created_at"
                ),
                {"correlation_id": correlation_id},
            )
            for row in planner_rows.mappings():
                duration_ms, outcome = _event_json_fields(row["event_json"])
                events.append(
                    SupportTimelineEvent(
                        source="planner_attempts",
                        event=str(row["event_kind"]),
                        occurred_at=row["created_at"],
                        correlation_id=correlation_id,
                        duration_ms=duration_ms,
                        outcome=outcome,
                    )
                )

            sql_rows = await self._session.execute(
                text(
                    "SELECT created_at, elapsed_ms, row_count, receipt_query_id "
                    "FROM sql_executions WHERE correlation_id = :correlation_id "
                    "ORDER BY created_at"
                ),
                {"correlation_id": correlation_id},
            )
            for row in sql_rows.mappings():
                events.append(
                    SupportTimelineEvent(
                        source="sql_executions",
                        event="query",
                        occurred_at=row["created_at"],
                        correlation_id=correlation_id,
                        duration_ms=row["elapsed_ms"],
                        row_count=row["row_count"],
                        receipt_query_id=row["receipt_query_id"],
                    )
                )

            invocation_rows = await self._session.execute(
                text(
                    "SELECT created_at, purpose, latency_ms "
                    "FROM model_invocations WHERE correlation_id = :correlation_id "
                    "ORDER BY created_at"
                ),
                {"correlation_id": correlation_id},
            )
            for row in invocation_rows.mappings():
                events.append(
                    SupportTimelineEvent(
                        source="model_invocations",
                        event=str(row["purpose"]),
                        occurred_at=row["created_at"],
                        correlation_id=correlation_id,
                        duration_ms=row["latency_ms"],
                    )
                )

        if answer_query_id is not None:
            event = (
                (
                    await self._session.execute(
                        text(
                            "SELECT created_at, answer_query_id "
                            "FROM business_query_execution_events "
                            "WHERE answer_query_id = :answer_query_id"
                        ),
                        {"answer_query_id": answer_query_id},
                    )
                )
                .mappings()
                .first()
            )
            if event is not None:
                events.append(
                    SupportTimelineEvent(
                        source="execution_events",
                        event="receipt",
                        occurred_at=event["created_at"],
                        correlation_id=correlation_id,
                        answer_query_id=event["answer_query_id"],
                    )
                )
                detail_rows = await self._session.execute(
                    text(
                        "SELECT ordinal, detail_digest, family, owner_resource, owner_ref_digest, "
                        "revision_digest, definition_digest, profile_digest, "
                        "coverage_status, coverage_digest, provenance_digest, created_at "
                        "FROM business_query_execution_detail_evidence "
                        "WHERE answer_query_id = :answer_query_id "
                        "ORDER BY ordinal"
                    ),
                    {"answer_query_id": answer_query_id},
                )
                for detail in detail_rows.mappings():
                    events.append(
                        SupportTimelineEvent(
                            source="detail_evidence",
                            event="detail",
                            occurred_at=detail["created_at"],
                            correlation_id=correlation_id,
                            answer_query_id=answer_query_id,
                            detail_digest=detail["detail_digest"],
                            detail_family=detail["family"],
                            detail_owner_resource=detail["owner_resource"],
                            detail_owner_ref_digest=detail["owner_ref_digest"],
                            detail_revision_digest=detail["revision_digest"],
                            detail_definition_digest=detail["definition_digest"],
                            detail_profile_digest=detail["profile_digest"],
                            detail_coverage_status=detail["coverage_status"],
                            detail_coverage_digest=detail["coverage_digest"],
                            detail_provenance_digest=detail["provenance_digest"],
                        )
                    )

        events.sort(key=lambda item: item.occurred_at or datetime.min)
        return SupportTimeline(
            correlation_id=correlation_id,
            answer_query_id=answer_query_id,
            events=tuple(events),
        )
