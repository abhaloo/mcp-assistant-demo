"""Publication service for committed Business Query results."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.business_query.ports import BusinessProgressSink, CommittedTable
from app.business_query.wire.ask_result import CommittedBqResult
from app.core.turn_budget import TurnBudget
from app.models.result_presentation import ResultPresentation

logger = logging.getLogger(__name__)


async def publish_committed_envelopes(
    envelopes: Sequence[Any],
    progress: BusinessProgressSink | None,
    budget: TurnBudget,
) -> None:
    """Enumerate and publish tables for committed envelopes."""
    budget.check_not_expired()
    if progress is None:
        return

    for ordinal, envelope in enumerate(envelopes):
        painted = getattr(progress, "painted_ordinals", frozenset())
        if ordinal in painted:
            continue

        # Check turn budget before each table frame
        budget.check_not_expired()

        if isinstance(envelope, dict):
            rows = list(envelope.get("rows") or [])
            raw_columns = envelope.get("columns") or ()
            presentation = envelope.get("presentation")
            total_row_count = envelope.get("total_row_count")
            aqid = envelope.get("answer_query_id") or ""
        else:
            rows = list(getattr(envelope, "rows", None) or [])
            raw_columns = getattr(envelope, "columns", None) or ()
            presentation = getattr(envelope, "presentation", None)
            total_row_count = getattr(envelope, "total_row_count", None)
            aqid = (
                getattr(envelope, "answer_query_id", None)
                or (getattr(getattr(envelope, "receipt", None), "answer_query_id", None))
                or ""
            )

        columns = tuple(raw_columns)

        # Fail closed if rows exist without declared columns
        if rows and not columns:
            if hasattr(progress, "disclosure_violation"):
                setattr(progress, "disclosure_violation", True)
            if hasattr(progress, "fail") and callable(progress.fail):
                progress.fail()
            return

        owns_table = bool(rows) or (presentation is not None and total_row_count == 0)
        if not owns_table:
            continue

        if isinstance(presentation, dict):
            try:
                presentation = ResultPresentation.model_validate(presentation)
            except Exception:
                presentation = None

        section = CommittedTable(
            ordinal=ordinal,
            tool_kind="business_query",
            answer_query_id=aqid,
            columns=columns,
            rows=tuple(rows),
            total_row_count=total_row_count if total_row_count is not None else len(rows),
            presentation=presentation,
        )
        progress.table(section)


async def publish_committed_bq(
    result: CommittedBqResult,
    progress: BusinessProgressSink | None,
    budget: TurnBudget,
) -> None:
    """Enumerate and publish tables for a committed BQ result.

    Only committed Answered results reach this publisher.
    Each table frame is bounded by TurnBudget.
    Deduplication prevents re-publishing already painted ordinals.
    """
    budget.check_not_expired()
    if progress is None:
        return
    if result is None or result.result is None:
        return
    if result.result.disposition != "answered":
        return

    wire = result.result.business_query
    if wire is None:
        return

    if wire.envelopes:
        envelopes = list(wire.envelopes)
    elif wire.envelope is not None:
        envelopes = [wire.envelope]
    else:
        envelopes = []
    if not envelopes:
        return

    await publish_committed_envelopes(envelopes, progress, budget)
