"""Structured Ask ladder branch — finish Business Query into an AskOutcome."""

from __future__ import annotations

from typing import Literal

from app.auth import Principal
from app.core.ask_errors import (
    CAPABILITY_UNAVAILABLE_MESSAGE,
    rethrow_model_route_denial,
)
from app.core.turn_budget import TurnBudget
from app.models.schemas import QueryType, Question
from app.providers.model_registry import PolicyViolationError
from app.providers.route_policy import RouteResolutionError
from app.resources import ProcessResources
from app.services.ask_outcome import (
    Answered,
    AskOutcome,
    CapabilityUnavailable,
    retrieved_from_sources,
)
from app.services.ask_prepare import PreparedTurn, bq_plan_question
from app.services.business_query_finish import build_guarded_rag_attach, finish_bq_policy


async def structured_outcome(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    turn: PreparedTurn,
    query_type: QueryType,
    progress: object | None,
    lifecycle_run_id: str | None,
    turn_budget: TurnBudget,
    fulfillment_scope: Literal["full", "structured_only"] | None = None,
    omitted_capabilities: tuple[str, ...] = (),
    banner: str | None = None,
    continuation_request_id: str | None = None,
) -> AskOutcome:
    ctx = turn.ctx
    access_tiers = turn.access_tiers
    stage_models = turn.stage_models
    owner_hint = turn.owner_hint
    config = {
        "metadata": {
            "role": principal.role,
            "query_type": query_type,
            "access_tiers": access_tiers,
        }
    }
    rag_attach = (
        build_guarded_rag_attach(
            ctx,
            access_tiers,
            config,
            principal=principal,
            turn_budget=turn_budget,
        )
        if query_type == "both"
        else None
    )
    bq_progress = progress if hasattr(progress, "emit") else None
    bq_reply = turn.bq_reply
    try:
        finish = await finish_bq_policy(
            resources=resources,
            question=bq_plan_question(body, turn),
            principal=principal,
            correlation_id=lifecycle_run_id or body.run_id or "",
            query_type=query_type,
            stage_models=stage_models,
            rag_attach=rag_attach,
            progress=bq_progress,
            owner_hint=owner_hint,
            response_policy=body.response_policy,
            idempotency_key=body.idempotency_key,
            continuation=bq_reply.continuation if bq_reply else None,
            clarification_reply=bq_reply.reply if bq_reply else None,
            clarification_prompt=bq_reply.prompt if bq_reply else None,
            history=turn.bq_history,
            turn_budget=turn_budget,
        )
    except (RouteResolutionError, PolicyViolationError) as exc:
        rethrow_model_route_denial(exc)
    if finish.bq.raise_capability_unavailable:
        return CapabilityUnavailable(
            detail=CAPABILITY_UNAVAILABLE_MESSAGE,
            answer=None,
            bq=finish.bq,
            ctx=ctx,
            turn_result=finish.bq.turn_result,
        )
    return Answered(
        answer_text=finish.answer_text,
        sources=retrieved_from_sources(finish.sources),
        query_type=query_type,
        citations=finish.citations,
        stage_models=stage_models,
        final_producer_purpose=finish.final_producer_purpose,
        ctx=ctx,
        sql_provenance=finish.sql_provenance,
        completion_status=finish.completion_status,
        sql_stop_reason=finish.sql_stop_reason,
        business_query=finish.bq.business_query,
        bq=finish.bq,
        disambiguation=finish.disambiguation,
        client_action=finish.client_action,
        fulfillment_scope=fulfillment_scope,
        omitted_capabilities=omitted_capabilities,
        banner=banner,
        continuation_request_id=continuation_request_id,
        presentation=finish.presentation,
        turn_result=finish.bq.turn_result,
    )
