"""One interactive planner round: question shaping, stall recovery, anti-loop."""

from __future__ import annotations

import logging
from typing import Any, Literal

from app.business_query.authorize.scoping import value_names_a_record
from app.business_query.definitions import DefinitionBundle, DimensionDefinition
from app.business_query.outcomes import ClarificationRequired, Incomplete
from app.business_query.plan.linked_relationships import (
    LINKED_RELATIONSHIPS,
    LinkedRelationship,
    find_child_relationship,
    resolve_linked_anaphora,
)
from app.business_query.plan.record_referents import (
    ExplicitRecordReference,
    redact_explicit_record_references,
)

__all__ = [
    "LINKED_RELATIONSHIPS",
    "LinkedRelationship",
    "PLANNER_TIMEOUT_CONTINUATION",
    "dialogue_for_request",
    "run_planner_round",
    "shape_question",
]
from app.business_query.ports import BusinessProgressSink, PlannerAdapter
from app.business_query.wire.outcome_rules import (
    clarification_exchange,
    effective_question,
    question_with_reading,
)
from app.business_query.wire.request import BusinessQueryOwnerHint, BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import TurnBudget, allowance_seconds, await_with_budget

logger = logging.getLogger(__name__)

# Continuation marker for the stall card a planner ceiling timeout gets.
# The producer keys the failed-step seal off the same spelling.
PLANNER_TIMEOUT_CONTINUATION = "planner-timeout"

_STALL_QUESTION = (
    "That is taking longer than usual. What detail can I "
    "narrow it by — a customer, a status, or a date range?"
)


def _explicit_ref_fallback(
    request: BusinessQueryRequest, ref: ExplicitRecordReference, redacted_q: str
) -> BusinessQueryRequest:
    child_rel = find_child_relationship(ref.resource, request.question)
    if child_rel is not None:
        return request.model_copy(update={"owner_hint": None})
    return request.model_copy(
        update={
            "owner_hint": BusinessQueryOwnerHint(
                resource_type=ref.resource,
                record_id=ref.record_id,
                binding_member=ref.binding_member,
            ),
            "question": redacted_q,
        }
    )


def _binding_dimension(
    bundle: DefinitionBundle, resource: str, member: str
) -> DimensionDefinition | None:
    """The bundle dimension a binding names, by its qualified name or by the
    bare member name under the bound resource."""
    qualified = member if "." in member else f"{resource}.{member}"
    return next(
        (
            item
            for item in bundle.dimensions
            if item.name == qualified and item.owning_resource == resource
        ),
        None,
    )


def _reference_dimension(bundle: DefinitionBundle, resource: str) -> DimensionDefinition | None:
    """The dimension a resource declares as its human-facing reference, if any."""
    binding = next((item for item in bundle.resources if item.name == resource), None)
    if binding is None or binding.reference_dimension is None:
        return None
    return _binding_dimension(bundle, resource, binding.reference_dimension)


def _shape_binding_question(
    request: BusinessQueryRequest, bundle: DefinitionBundle
) -> BusinessQueryRequest:
    hint = request.owner_hint
    if hint is None:
        return request

    # A value that names no record through its member (a date, or a value of the
    # wrong kind) never becomes a filter; the planner then plans from the reading
    # alone. The scope step applies the same rule to a plan that skips this round.
    # A member the resource does not own never widens the plan: the record
    # keeps its filter through the resource's reference dimension, or keeps the
    # unknown member for the scope step to refuse.
    if hint.binding_member is not None:
        dim = _binding_dimension(bundle, hint.resource_type, hint.binding_member)
        if dim is None:
            dim = _reference_dimension(bundle, hint.resource_type)
            logger.warning(
                "binding member unknown to its resource member=%s resource=%s rebound=%s",
                hint.binding_member,
                hint.resource_type,
                dim.name if dim is not None else None,
            )
        if dim is not None and not value_names_a_record(dim.type, str(hint.record_id)):
            logger.info(
                "binding dropped: value names no record member=%s kind=%s",
                hint.binding_member,
                dim.type,
            )
            request = request.model_copy(update={"owner_hint": None})
        elif dim is not None:
            request = request.model_copy(
                update={"owner_hint": hint.model_copy(update={"binding_member": dim.name})}
            )

    # The person's own words win: an explicit reference to another record drops the binding.
    redacted_q, ref = redact_explicit_record_references(request.question, bundle)
    if ref is None:
        return request

    if request.owner_hint is not None:
        if (ref.resource, str(ref.record_id)) != (
            request.owner_hint.resource_type,
            str(request.owner_hint.record_id),
        ):
            logger.info(
                "binding dropped: question names another record resource=%s record_id=%s",
                ref.resource,
                ref.record_id,
            )
            request = request.model_copy(update={"owner_hint": None})
            return _explicit_ref_fallback(request, ref, redacted_q)
        # The question names the bound record itself; the filter stays even for a
        # child-noun question, so the scoped plan carries the record predicate.
        return request.model_copy(update={"question": redacted_q})

    return _explicit_ref_fallback(request, ref, redacted_q)


def _shape_page_question(
    request: BusinessQueryRequest, bundle: DefinitionBundle
) -> BusinessQueryRequest | ClarificationRequired:
    # Page hint or no hint: existing flow
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
        if (request.owner_hint.resource_type, str(request.owner_hint.record_id)) != (
            ref.resource,
            str(ref.record_id),
        ):
            return ClarificationRequired(
                question=(
                    "The question refers to a different record than the active page context."
                ),
                continuation="explicit_record_conflict",
            )
        return request.model_copy(update={"question": redacted_q})

    return request.model_copy(
        update={
            "owner_hint": BusinessQueryOwnerHint(
                resource_type=ref.resource,
                record_id=ref.record_id,
                binding_member=ref.binding_member,
            ),
            "question": redacted_q,
        }
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

    if request.owner_hint is not None and request.owner_hint.source == "binding":
        # A binding names a record the person already identified; the page-context
        # anaphora rewrite ("this invoice") does not apply to it.
        return _shape_binding_question(request, bundle)

    return _shape_page_question(request, bundle)


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
    choice_suggester: Any = None,
    terminal_reserve_seconds: float = 2.0,
) -> Any:
    """Plan one turn; a ceiling stall asks the suggestion port for options.

    A clarification reply re-plans as dialogue — the shown-question/reply
    pair rides ``clarification_exchange`` while the primary message keeps the
    original question, so the prompt prefix stays byte-identical for the
    provider prompt cache. Tickets without the shown prompt fall back to the
    ``effective_question`` text merge. The continuation marker keeps a second
    stall a real timeout and a repeated clarification ``no_progress``.
    The planning reserve stays intact whichever bound stops the planner.
    Empty suggestion output is a typed timeout, never an empty card.
    Only a turn with no planning allowance left raises ``DeadlineExpiredError``.
    """
    from app.business_query.wire.clarification_suggestions import (
        SuggestionContext,
        clarification_with_suggestions,
    )

    question = effective_question(request)
    exchange = clarification_exchange(request)
    if exchange is not None:
        question = request.question or ""
    question = question_with_reading(question, request.reading)
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
        return await clarification_with_suggestions(
            question=_STALL_QUESTION,
            continuation=PLANNER_TIMEOUT_CONTINUATION,
            context=SuggestionContext(
                user_question=question,
                dialogue=dialogue,
                turn_budget=turn_budget,
                terminal_reserve_seconds=terminal_reserve_seconds,
                suggester=choice_suggester,
            ),
        )

    if isinstance(planned, ClarificationRequired) and request.continuation is not None:
        return Incomplete(reason_code="no_progress")
    if (
        isinstance(planned, ClarificationRequired)
        and not planned.choices
        and choice_suggester is not None
    ):
        return await clarification_with_suggestions(
            question=planned.question,
            continuation=planned.continuation,
            context=SuggestionContext(
                user_question=question,
                dialogue=dialogue,
                turn_budget=turn_budget,
                terminal_reserve_seconds=terminal_reserve_seconds,
                suggester=choice_suggester,
            ),
        )
    return planned
