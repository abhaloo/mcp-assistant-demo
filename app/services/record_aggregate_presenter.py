"""Deterministic user copy for the bounded R3 aggregate continuation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.policy.record_tools import RecordCountResult
from app.services.record_intent import RecordAggregateContinuation


@dataclass(frozen=True)
class RecordAggregatePresentation:
    answer: str
    kind: Literal["resolved", "zero_rows"]
    clarification_allowed: bool = False


def _plural_resource(resource_type: str, count: int) -> str:
    label = resource_type.replace("_", " ")
    return label if count == 1 else f"{label}s"


def present_record_aggregate(
    continuation: RecordAggregateContinuation,
) -> RecordAggregatePresentation:
    """Render only the typed aggregate fields; no model or SQL is involved."""
    if isinstance(continuation.result, RecordCountResult):
        resource = _plural_resource(
            continuation.intent.resource_type or "record", continuation.result.count
        )
        if continuation.result.count == 0:
            return RecordAggregatePresentation(
                answer=f"No {resource} matched that request.", kind="zero_rows"
            )
        verb = "is" if continuation.result.count == 1 else "are"
        return RecordAggregatePresentation(
            answer=f"There {verb} {continuation.result.count} {resource} matching that request.",
            kind="resolved",
        )

    resource = _plural_resource(continuation.intent.resource_type or "record", 2)
    group_by = continuation.intent.group_by or "group"
    heading = f"{resource.capitalize()} by {group_by.replace('_', ' ')}:"
    if not continuation.result.groups:
        return RecordAggregatePresentation(
            answer=f"No {resource} matched that request.", kind="zero_rows"
        )
    lines = "\n".join(f"- {group.label}: {group.count}" for group in continuation.result.groups)
    return RecordAggregatePresentation(answer=f"{heading}\n{lines}", kind="resolved")


def present_record_dispatch_empty(outcome: str) -> RecordAggregatePresentation:
    """Keep empty, unsupported, and transient outcomes honestly distinct."""
    if outcome == "measure_clarification":
        return RecordAggregatePresentation(
            answer=(
                "Which non-financial measure do you mean: quantity, dimensions, duration, "
                "or another measure?"
            ),
            kind="zero_rows",
            clarification_allowed=True,
        )
    if outcome == "operation_unsupported":
        return RecordAggregatePresentation(
            answer="I can't calculate financial amounts from the records I can use yet.",
            kind="zero_rows",
        )
    if outcome == "tool_error":
        return RecordAggregatePresentation(
            answer="I couldn't complete that record lookup right now. Please try again.",
            kind="zero_rows",
        )
    return RecordAggregatePresentation(
        answer="I wasn't able to answer that from the records I can look up.", kind="zero_rows"
    )
