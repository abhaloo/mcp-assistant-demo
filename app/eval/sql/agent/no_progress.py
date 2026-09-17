"""No-progress stop: detects an agent re-running the same SQL shape with no new evidence.

Fingerprints normalized SQL STRUCTURE only -- never the question, a case id, or a
domain phrase. This is the deliberate counter-example to sql_shape_validate's
_EXISTENCE_LIST_ASK_RE, which is keyed to one gold question's wording.
"""

from __future__ import annotations

import re
from typing import Any

from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded

# Repeats of the same fingerprint with unchanged evidence before the run stops.
NO_PROGRESS_STOP_AFTER = 3

_WHITESPACE_RE = re.compile(r"\s+")


def fingerprint_sql(sql: str) -> str:
    """Normalize sql_db_query text: collapse whitespace, uppercase, drop a trailing ';'.

    Literal values are kept -- LIMIT 20 and LIMIT 50 must fingerprint differently, or
    genuine refinement (a narrower filter, a bigger page) reads as a stall.
    """
    collapsed = _WHITESPACE_RE.sub(" ", (sql or "").strip())
    return collapsed.rstrip(";").rstrip().upper()


def observe_sql_execution(
    seen: dict[str, dict[str, Any]],
    sql: str,
    *,
    row_count: int,
    truncated: bool,
) -> dict[str, dict[str, Any]]:
    """Record one sql_db_query execution against the run's fingerprint seen-set.

    Returns the updated seen-set -- callers own state accumulation (SqlAgentState has
    no reducer for this field; the graph node returns the whole value each hop, same
    as shape_retries/list_query_streak).

    Raises SqlContextBudgetExceeded("stall_no_progress") once the same fingerprint has
    repeated NO_PROGRESS_STOP_AFTER times in a row with no new evidence (same row_count
    and truncation state). A row count or truncation change is progress and resets the
    streak rather than merely failing to increment it.
    """
    fingerprint = fingerprint_sql(sql)
    prior = seen.get(fingerprint)
    if prior is not None and prior["row_count"] == row_count and prior["truncated"] == truncated:
        streak = int(prior["streak"]) + 1
    else:
        streak = 0
    if streak >= NO_PROGRESS_STOP_AFTER:
        raise SqlContextBudgetExceeded("stall_no_progress")
    updated = dict(seen)
    updated[fingerprint] = {"row_count": row_count, "truncated": truncated, "streak": streak}
    return updated
