"""One interactive planner round: question shaping, stall recovery, anti-loop."""

from __future__ import annotations

from typing import Any, Literal

from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import ClarificationRequired, Incomplete
from app.business_query.plan.linked_relationships import (
    LINKED_RELATIONSHIPS,
    LinkedRelationship,
    find_child_relationship,
    resolve_linked_anaphora,
)
from app.business_query.plan.record_referents import redact_explicit_record_references

__all__ = [
    "LINKED_RELATIONSHIPS",
    "LinkedRelationship",
    "PLANNER_TIMEOUT_CONTINUATION",
    "dialogue_for_request",
    "run_planner_round",
    "shape_question",
]
from app.business_query.ports import BusinessProgressSink, PlannerAdapter
from app.business_query.wire.outcome_rules import clarification_exchange, effective_question
from app.business_query.wire.request import BusinessQueryOwnerHint, BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import TurnBudget, allowance_seconds, await_with_budget

# Continuation marker for the stall card a planner ceiling timeout gets.
# The producer keys the failed-step seal off the same spelling.
PLANNER_TIMEOUT_CONTINUATION = "planner-timeout"

_STALL_QUESTION = (
    "That is taking longer than usual. What detail can I "
    "narrow it by — a customer, a status, or a date range?"
)


def shape_question(
    request: BusinessQueryRequest, bundle: DefinitionBundle
) -> BusinessQueryRequest | ClarificationRequired:
    """Redact explicit record references and bind owner hints for self-queries.

    Child-grain queries never set owner_hint to a parent entity to avoid
    grain_unexpressible denials. Anaphoric follow-up queries resolve through
    prior history and relationship links.
    """
    if request.question is None:
        return request

    # Check for anaphoric follow-up reference to prior turn antecedent
    anaphoric_request = resolve_linked_anaphora(request, bundle)
    if anaphoric_request is not None:
        return anaphoric_request

    redacted_q, ref = redact_explicit_record_references(request.question, bundle)
    if ref is None:
        return request

    # If the question asks for child records of this referenced resource,
    # keep scoping filter-based and omit owner_hint.
    child_rel = find_child_relationship(ref.resource, request.question)
    if child_rel is not None:
        return request.model_copy(update={"owner_hint": None})

    if request.owner_hint is not None:
        if request.owner_hint.resource_type != ref.resource or str(
            request.owner_hint.record_id
        ) != str(ref.record_id):
            return ClarificationRequired(
                question=(
                    "The question refers to a different record than the active page context."
                ),
                continuation="explicit_record_conflict",
            )
    else:
        request = request.model_copy(
            update={
                "owner_hint": BusinessQueryOwnerHint(
                    resource_type=ref.resource,
                    record_id=ref.record_id,
                    binding_member=ref.binding_member,
                )
            }
        )
    return request.model_copy(update={"question": redacted_q})


def dialogue_for_request(
    request: BusinessQueryRequest,
) -> tuple[tuple[Literal["ai", "human"], str], ...] | None:
    """Validate ``request.history`` as the prior-turn dialogue for ``planner.plan``.

    Empty history ⇒ ``None`` so the planner prompt stays byte-identical to
    ``build_planner_prompt``. The clarification exchange is NOT part of this
    dialogue: it continues the current turn and rides ``planner.plan``'s own
    ``clarification_exchange`` parameter, positioned after the question.
    """
    parts: list[tuple[Literal["ai", "human"], str]] = []
    for role, text in request.history:
        if role not in ("ai", "human"):
            raise ValueError(f"unsupported dialogue role: {role!r}")
        parts.append((role, text))
    if not parts:
        return None
    return tuple(parts)


async def run_planner_round(
    *,
    planner: PlannerAdapter,
    request: BusinessQueryRequest,
    card: str,
    timeout: float,
    trace: QueryTrace,
    turn_budget: TurnBudget,
    reserve_seconds: float,
    progress: BusinessProgressSink | None = None,
) -> Any:
    """Plan one turn; a ceiling stall becomes the static stall card.

    A clarification reply re-plans as dialogue — the shown-question/reply
    pair rides ``clarification_exchange`` while the primary message keeps the
    original question, so the prompt prefix stays byte-identical for the
    provider prompt cache. Tickets without the shown prompt fall back to the
    ``effective_question`` text merge. The continuation marker keeps a second
    stall a real timeout and a repeated clarification ``no_progress``.
    The planning reserve stays intact whichever bound stops the planner, so
    a budget-selected expiry ends in the same stall card. Only a turn with no
    planning allowance left raises ``DeadlineExpiredError`` to the module.
    """
    question = effective_question(request)
    exchange = clarification_exchange(request)
    if exchange is not None:
        question = request.question or ""
    dialogue = dialogue_for_request(request)
    allowance = allowance_seconds(
        turn_budget, ceiling_seconds=timeout, reserve_seconds=reserve_seconds
    )
    try:
        planned = await await_with_budget(
            lambda: planner.plan(
                question,
                card,
                business_date=request.business_date,
                retry_hint=None,
                trace=trace,
                clarification_exchange=exchange,
                dialogue=dialogue,
                progress=progress,
            ),
            turn_budget,
            ceiling_seconds=timeout,
            reserve_seconds=reserve_seconds,
        )
    except (TimeoutError, DeadlineExpiredError):
        if allowance <= 0:
            raise
        if request.continuation is not None:
            return Incomplete(reason_code="timeout")
        return ClarificationRequired(
            question=_STALL_QUESTION,
            continuation=PLANNER_TIMEOUT_CONTINUATION,
            choices=[],
        )

    if isinstance(planned, ClarificationRequired) and request.continuation is not None:
        return Incomplete(reason_code="no_progress")
    return planned
