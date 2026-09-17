"""Ask composition root for Business Query — maps Module outcomes to Ask DTOs."""

from __future__ import annotations

import logging
from typing import Literal

from app.auth import Principal
from app.business_query.composition import (
    build_module as build_module,
)
from app.business_query.composition import (
    business_query_evidence_config as business_query_evidence_config,
)
from app.business_query.definitions import (
    BundleSelectionError,
)
from app.business_query.definitions import (
    bundle_for_manifest as bundle_for_manifest,
)
from app.business_query.plan.attempts import AttemptConflictError
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.business_query.wire.module import (
    BusinessProgressSink,
    BusinessQueryOwnerHint,
)
from app.business_query.wire.request import ResultPageRequest, build_new_business_query_wire
from app.config import settings
from app.core.ask_errors import (
    CAPABILITY_UNAVAILABLE_MESSAGE,
)
from app.core.ask_errors import (
    EVIDENCE_UNAVAILABLE_MESSAGE as EVIDENCE_UNAVAILABLE_MESSAGE,
)
from app.core.errors import CapabilityUnavailableError
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.resources import ProcessResources
from app.services.business_query_mapping import (
    _RECORD_DATABASE_IDENTITY as _RECORD_DATABASE_IDENTITY,
)
from app.services.business_query_mapping import (
    _UNSUPPORTED_ANSWER as _UNSUPPORTED_ANSWER,
)
from app.services.business_query_mapping import (
    BQ_COMPOSITION_ERRORS,
    composition_failed_result,
    request_conflict_result,
)
from app.services.business_query_mapping import (
    UNSUPPORTED_MESSAGE as UNSUPPORTED_MESSAGE,
)
from app.services.business_query_mapping import (
    _denied_capability_result as _denied_capability_result,
)
from app.services.business_query_mapping import (
    _evidence_unavailable_result as _evidence_unavailable_result,
)
from app.services.business_query_mapping import (
    _map_outcome as _map_outcome,
)
from app.services.business_query_mapping import (
    denied_capability_result as denied_capability_result,
)
from app.services.business_query_mapping import (
    evidence_unavailable_result as evidence_unavailable_result,
)
from app.services.business_query_mapping import (
    map_outcome as map_outcome,
)
from app.services.business_query_operation import (
    PreparedBqOperation,
)
from app.services.business_query_operation import (
    _abort_unanswered_uow as _abort_unanswered_uow,
)
from app.services.business_query_operation import (
    _answer_query_ids as _answer_query_ids,
)
from app.services.business_query_operation import (
    _build_module as _build_module,
)
from app.services.business_query_operation import (
    _map_and_require_query_record as _map_and_require_query_record,
)
from app.services.business_query_operation import (
    abort_unanswered_uow as abort_unanswered_uow,
)
from app.services.business_query_operation import (
    build_bq_module as build_bq_module,
)
from app.services.business_query_operation import (
    map_and_require_query_record as map_and_require_query_record,
)
from app.services.business_query_publication import publish_committed_bq
from app.services.business_query_runtime import (
    evidence_context as _evidence_context,
)
from app.services.business_query_runtime import (
    module_query as _module_query,
)
from app.services.business_query_telemetry import (
    persist_answered_query_record as persist_answered_query_record,
)

logger = logging.getLogger(__name__)

__all__ = ["AskBusinessQueryResult", "CommittedBqResult"]


async def run_business_query_for_ask(
    *,
    question: str,
    principal: Principal,
    resources: ProcessResources,
    correlation_id: str,
    continuation: str | None = None,
    clarification_reply: str | None = None,
    clarification_prompt: str | None = None,
    progress: BusinessProgressSink | None = None,
    owner_hint: BusinessQueryOwnerHint | None = None,
    response_policy: Literal["allow_partial", "strict"] = "allow_partial",
    idempotency_key: str | None = None,
    history: tuple[tuple[str, str], ...] = (),
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
) -> AskBusinessQueryResult:
    """Shared Ask-layer BQ policy: disabled→503; shadow/enabled→Module."""
    if settings.business_query_mode == "disabled":
        raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)
    return await compose_business_query_answer(
        resources=resources,
        question=question,
        principal=principal,
        correlation_id=correlation_id,
        continuation=continuation,
        clarification_reply=clarification_reply,
        clarification_prompt=clarification_prompt,
        progress=progress,
        owner_hint=owner_hint,
        response_policy=response_policy,
        idempotency_key=idempotency_key,
        history=history,
        turn_budget=turn_budget,
    )


def build_prepared_bq_operation(
    *,
    question: str,
    principal: Principal,
    resources: ProcessResources,
    correlation_id: str,
    continuation: str | None = None,
    clarification_reply: str | None = None,
    clarification_prompt: str | None = None,
    progress: BusinessProgressSink | None = None,
    owner_hint: BusinessQueryOwnerHint | None = None,
    response_policy: Literal["allow_partial", "strict"] = "allow_partial",
    idempotency_key: str | None = None,
    history: tuple[tuple[str, str], ...] = (),
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
) -> PreparedBqOperation:
    """One request-scoped BQ operation bound to the caller's authorities.

    Both the legacy Ask path and the coordinator's ``query_business`` tool
    build their operation here. Composition errors propagate; the caller
    decides how a refusal reads.
    """
    request = build_new_business_query_wire(
        question=question,
        principal=principal,
        correlation_id=correlation_id,
        continuation=continuation,
        clarification_reply=clarification_reply,
        clarification_prompt=clarification_prompt,
        owner_hint=owner_hint,
        response_policy=response_policy,
        history=history,
    )
    evidence = None
    if settings.query_record_database_url.strip():
        evidence = _evidence_context(
            correlation_id,
            principal=principal,
            question=request.question,
            idempotency_key=idempotency_key,
            response_policy=request.response_policy,
            owner_hint=owner_hint,
        )
    return PreparedBqOperation(
        request,
        resources=resources,
        progress=progress,
        evidence=evidence,
        turn_budget=turn_budget,
        create_module=lambda session: _build_module(
            principal,
            resources=resources,
            correlation_id=correlation_id,
            attempt_session=session,
        ),
    )


async def compose_business_query_answer(
    *,
    question: str,
    principal: Principal,
    resources: ProcessResources,
    correlation_id: str,
    continuation: str | None = None,
    clarification_reply: str | None = None,
    clarification_prompt: str | None = None,
    progress: BusinessProgressSink | None = None,
    owner_hint: BusinessQueryOwnerHint | None = None,
    response_policy: Literal["allow_partial", "strict"] = "allow_partial",
    idempotency_key: str | None = None,
    history: tuple[tuple[str, str], ...] = (),
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
) -> AskBusinessQueryResult:
    try:
        operation = build_prepared_bq_operation(
            question=question,
            principal=principal,
            resources=resources,
            correlation_id=correlation_id,
            continuation=continuation,
            clarification_reply=clarification_reply,
            clarification_prompt=clarification_prompt,
            progress=progress,
            owner_hint=owner_hint,
            response_policy=response_policy,
            idempotency_key=idempotency_key,
            history=history,
            turn_budget=turn_budget,
        )
        return await _module_query(
            operation,
            operation.request,
            progress=progress,
            turn_budget=turn_budget,
        )
    except (CapabilityUnavailableError, BundleSelectionError):
        return _denied_capability_result()
    except AttemptConflictError:
        return request_conflict_result(correlation_id=correlation_id)
    except BQ_COMPOSITION_ERRORS as exc:
        return composition_failed_result(exc, correlation_id=correlation_id)


async def compose_business_query_page_answer(
    *,
    page_cursor: str,
    principal: Principal,
    resources: ProcessResources,
    correlation_id: str,
    max_rows: int,
    idempotency_key: str | None,
    turn_budget: TurnBudget,
    progress: BusinessProgressSink | None = None,
) -> AskBusinessQueryResult:
    """Execute a signed result-page continuation through the service seam."""
    from app.db.postgres import session_scope

    async with session_scope() as attempt_session:
        module = _build_module(
            principal,
            resources=resources,
            correlation_id=correlation_id,
            attempt_session=attempt_session,
        )
        request = ResultPageRequest(
            page_cursor=page_cursor,
            principal=principal,
            correlation_id=correlation_id,
            max_rows=max_rows,
        ).as_wire()
        evidence = _evidence_context(
            correlation_id,
            principal=principal,
            page_cursor=page_cursor,
            idempotency_key=idempotency_key,
            response_policy=request.response_policy,
        )
        outcome = await module.query(request, evidence=evidence, turn_budget=turn_budget)
        mapped = await _map_and_require_query_record(
            outcome,
            shadow=False,
            evidence_sealed=evidence is not None,
            question="",
            principal=principal,
            correlation_id=correlation_id,
            module=module,
        )
        await _abort_unanswered_uow(attempt_session, mapped)

    if mapped.disposition == "answered":
        if progress is not None and hasattr(progress, "emit"):
            progress.emit("authorized")
        committed = CommittedBqResult(
            result=mapped,
            answer_queries=_answer_query_ids(outcome),
        )
        await publish_committed_bq(committed, progress=progress, budget=turn_budget)
    return mapped
