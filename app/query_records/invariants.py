"""Cross-record invariants over the query-record trace store.

The trace store records every planner prompt and every answered turn. These
checks state properties that must hold for every conversation, so a
conversation-logic defect surfaces as a finding instead of waiting for a
person to notice a wrong answer. Pure functions over already-fetched rows;
the CLI in ``scripts/smoke/trace_invariants.py`` owns the SQL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "InvariantFinding",
    "check_distinct_questions_distinct_plans",
    "check_question_last",
    "check_trace_sub_query_compatibility",
]

# A row carrying this code intentionally repeats an earlier plan. The code is
# part of the planned reason vocabulary; until a writer emits it the exemption
# simply never fires.
_DUPLICATE_CODE = "duplicate_request"

# Prior-turn answers reach the planner as digest text with this prefix
# (app/services/ask_prepare.py). One appearing AFTER the card question means
# history was placed after the question being planned.
_HISTORY_DIGEST_PREFIX = "answered:"

_QUESTION_MARKER = "\nQuestion: "


@dataclass(frozen=True)
class InvariantFinding:
    invariant: str
    correlation_ids: tuple[str, ...]
    detail: str


def check_question_last(
    _raw_question: str | None,
    correlation_id: str,
    request_messages: list[dict[str, Any]],
) -> InvariantFinding | None:
    """The card question must be the prompt's pivot; nothing conversational may
    invert time around it.

    Three violations, each a shape of the same defect class (history placed
    after the question being planned):

    1. The prompt ends on an ``ai`` message — the model continues its own
       earlier turn (the recorded 2026-08-31 inversion).
    2. No human message carries the card question marker at all.
    3. A history-digest ``ai`` message (``answered:`` prefix) appears after the
       marker-bearing human message — a prior turn's answer placed after the
       current question.

    Role and marker shapes, not question text: record-reference redaction
    rewrites the question before it reaches the card, so text equality against
    the unused first argument false-positives on redacted turns. Repair hints, stall
    instructions, and clarification replies legitimately follow the marker as
    ``ai``/``human`` pairs and stay clean under all three rules.
    """
    if not request_messages:
        return None
    if request_messages[-1].get("type") != "human":
        return InvariantFinding(
            invariant="question_last",
            correlation_ids=(correlation_id,),
            detail=(
                f"planner prompt ends on a {request_messages[-1].get('type')!r} "
                "message; the model is continuing its own earlier turn"
            ),
        )
    marker_index: int | None = None
    for i, message in enumerate(request_messages):
        if message.get("type") == "human" and _QUESTION_MARKER in str(message.get("content", "")):
            marker_index = i
    if marker_index is None:
        return InvariantFinding(
            invariant="question_last",
            correlation_ids=(correlation_id,),
            detail="no human message carries the card question marker",
        )
    for message in request_messages[marker_index + 1 :]:
        is_digest = message.get("type") == "ai" and str(message.get("content", "")).startswith(
            _HISTORY_DIGEST_PREFIX
        )
        if is_digest:
            return InvariantFinding(
                invariant="question_last",
                correlation_ids=(correlation_id,),
                detail=(
                    "a prior-turn answer digest follows the card question; "
                    "history was placed after the question being planned"
                ),
            )
    return None


def _fingerprint_set(row: dict[str, Any]) -> frozenset[str] | None:
    fps = row.get("plan_fingerprints")
    if fps is not None:
        values = frozenset(str(item) for item in fps if item)
        return values if values else None
    fp = row.get("plan_fingerprint")
    if not fp:
        return None
    return frozenset({str(fp)})


def check_trace_sub_query_compatibility(
    payload: dict[str, Any], correlation_id: str
) -> InvariantFinding | None:
    """When ``sub_queries`` is present, top-level fingerprint is first and
    top-level rows equal the sum. Absent ``sub_queries`` is legacy scalar.
    """
    subs = payload.get("sub_queries")
    if not isinstance(subs, list) or not subs:
        return None
    first_fp = subs[0].get("plan_fingerprint")
    if payload.get("plan_fingerprint") != first_fp:
        return InvariantFinding(
            invariant="sub_query_trace_shape",
            correlation_ids=(correlation_id,),
            detail="top-level plan_fingerprint is not the first sub-query fingerprint",
        )
    summed = sum(int(item.get("rows_returned") or 0) for item in subs)
    top_rows = payload.get("rows_returned")
    if top_rows is not None and int(top_rows) != summed:
        return InvariantFinding(
            invariant="sub_query_trace_shape",
            correlation_ids=(correlation_id,),
            detail="top-level rows_returned is not the sum of sub-query rows",
        )
    return None


def check_distinct_questions_distinct_plans(
    rows: list[dict[str, Any]],
) -> list[InvariantFinding]:
    """Adjacent turns of one thread asking different questions must not commit
    the same plan.

    ``rows`` need: correlation_id, raw_question, thread_id, created_at
    (sortable), stable_error_code, and either ``plan_fingerprints`` (a set of
    fingerprints when ``sub_queries`` exists) or scalar ``plan_fingerprint``.
    Rows without a thread id, fingerprints, or question text are skipped —
    unthreaded rows predate the thread stamp, refusals carry no plan, and chip
    turns carry no words. Adjacent-only on purpose: within a thread, a later
    turn may legitimately return to an earlier plan ("show me the first one
    again"); the defect this catches is consecutive different questions answered
    identically. A row marked ``duplicate_request`` repeats intentionally and is
    exempt. When both turns carry fingerprint sets, equality is set equality
    so a singleton {X} adjacent to {X, Y} is not a finding.
    """
    findings: list[InvariantFinding] = []
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not row.get("thread_id"):
            continue
        if not row.get("raw_question"):
            continue
        if _fingerprint_set(row) is None:
            continue
        by_thread.setdefault(str(row["thread_id"]), []).append(row)

    for turns in by_thread.values():
        turns.sort(key=lambda r: r["created_at"])
        for earlier, later in zip(turns, turns[1:], strict=False):
            if earlier["raw_question"] == later["raw_question"]:
                continue
            if _fingerprint_set(earlier) != _fingerprint_set(later):
                continue
            if _DUPLICATE_CODE in (
                earlier.get("stable_error_code"),
                later.get("stable_error_code"),
            ):
                continue
            sample = next(iter(_fingerprint_set(earlier) or ()))
            findings.append(
                InvariantFinding(
                    invariant="distinct_questions_distinct_plans",
                    correlation_ids=(
                        str(earlier["correlation_id"]),
                        str(later["correlation_id"]),
                    ),
                    detail=(
                        "adjacent turns of one thread asked different questions "
                        "and committed the same plan fingerprint "
                        f"{sample[:16]}…"
                    ),
                )
            )
    return findings
