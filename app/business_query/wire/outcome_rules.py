"""Outcome determination and currency constraints for wire orchestration."""

from __future__ import annotations

from hashlib import sha256
from typing import get_args

from app.business_query.authorize.capability import visible_members
from app.business_query.authorize.preconditions import check_native_currency_for_measure
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import (
    BusinessQueryOutcome,
    ClarificationRequired,
    Denied,
    Incomplete,
    PlanRefused,
    Unsupported,
)
from app.business_query.plan import BusinessQueryPlan, guaranteed_filter_members
from app.business_query.wire.request import BusinessQueryRequest
from app.business_query.wire.trace import QueryTrace

_UNSUPPORTED_CODES: frozenset[str] = frozenset(
    get_args(Unsupported.model_fields["reason_code"].annotation)
)
_INCOMPLETE_ONLY_CODES: frozenset[str] = frozenset(
    {"invalid_business_timezone", "unsupported_relative_period"}
)
_DENY_MESSAGE = "business query tools are currently unavailable"


def effective_question(request: BusinessQueryRequest) -> str:
    if request.continuation is None:
        return request.question or ""
    reply = (request.clarification_reply or "").strip()
    if not reply:
        return request.question or ""
    return f"{request.question}\n{reply}"


def clarification_exchange(request: BusinessQueryRequest) -> tuple[str, str] | None:
    """The shown-question/reply pair a re-plan sends as dialogue turns.

    None when any part is missing — legacy tickets without the shown prompt
    fall back to the ``effective_question`` text merge.
    """
    if request.continuation is None or request.clarification_prompt is None:
        return None
    reply = (request.clarification_reply or "").strip()
    if not reply:
        return None
    return (request.clarification_prompt, reply)


def plan_refused_to_outcome(exc: PlanRefused, trace: QueryTrace) -> BusinessQueryOutcome:
    code = exc.reason_code
    if code in _UNSUPPORTED_CODES:
        trace.fail("execute", code, grain_check_site=exc.check_site)
        return Unsupported(reason_code=code, message="plan cannot be executed safely")
    assert code in _INCOMPLETE_ONLY_CODES
    return Incomplete(reason_code="adapter_invalid")


def required_filters_present(plan: BusinessQueryPlan, bundle: DefinitionBundle) -> bool:
    supplied = guaranteed_filter_members(plan.filters)
    required: set[str] = set()
    selected = set(plan.measures) | set(plan.dimensions)
    if plan.bucket_set:
        selected.add(plan.bucket_set)
    for capability in bundle.capabilities:
        if capability.name in selected:
            required.update(capability.required_filters)
    return required.issubset(supplied)


def native_currency_outcome(
    plan: BusinessQueryPlan,
    request: BusinessQueryRequest,
    bundle: DefinitionBundle,
    trace: QueryTrace,
) -> ClarificationRequired | Denied | Incomplete | Unsupported | None:
    measures = {measure.name: measure for measure in bundle.measures}
    capabilities = {entry.name: entry for entry in bundle.capabilities}
    visible = visible_members(request.principal, bundle)

    for measure_member in plan.measures:
        entry = capabilities.get(measure_member)
        measure = measures.get(entry.resolves_to) if entry is not None else None
        if measure is None:
            continue
        currency = measure.currency_dimension
        if currency is None and measure.format != "currency":
            continue
        violation = check_native_currency_for_measure(
            measure_member=measure_member,
            currency_dimension=currency,
            plan=plan,
            allowed=visible,
            bundle=bundle,
        )
        if violation is None:
            continue
        if violation.kind == "not_declared":
            trace.fail("execute", "capability_disabled")
            return Unsupported(
                reason_code="capability_disabled", message="plan cannot be executed safely"
            )
        if violation.kind == "alias_not_visible":
            return Denied(message=_DENY_MESSAGE)
        if request.continuation is not None:
            return Incomplete(reason_code="no_progress")
        continuation_input = f"{request.correlation_id}:{measure_member}"
        continuation = sha256(continuation_input.encode()).hexdigest()[:16]
        return ClarificationRequired(
            question="Which currency should I use, or should I break the result down by currency?",
            continuation=continuation,
        )
    return None
