"""JSON transport adapter for Business Query Ask turns.

Symmetric with ``business_query_stream.produce_bq_stream`` (SSE): this module
owns the JSON front door, mapping the shared finish policy
(``business_query_finish.finish_bq_policy``) onto the Ask ``Answer`` DTO.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.services.ask_service import make_answer
from app.services.business_query_finish import build_guarded_rag_attach, finish_bq_policy

if TYPE_CHECKING:
    from app.auth import Principal
    from app.business_query.wire.module import BusinessQueryOwnerHint
    from app.conversation.turn import TurnContext
    from app.models.schemas import Answer, Question
    from app.providers.stage_model_report import StageModelAccumulator
    from app.resources import ProcessResources
    from app.services.business_query_service import AskBusinessQueryResult


async def produce_bq_answer(
    *,
    body: Question,
    ctx: TurnContext,
    principal: Principal,
    resources: ProcessResources,
    query_type: str,
    access_tiers: list[str],
    config: dict,
    stage_models: StageModelAccumulator,
    correlation_id: str,
    owner_hint: BusinessQueryOwnerHint | None = None,
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
) -> tuple[Answer, AskBusinessQueryResult]:
    """Map classifier structured/both through Business Query for JSON Ask."""
    rag_attach = (
        build_guarded_rag_attach(
            ctx, access_tiers, config, principal=principal, turn_budget=turn_budget
        )
        if query_type == "both"
        else None
    )
    finish = await finish_bq_policy(
        resources=resources,
        question=body.question or ctx.original_question,
        principal=principal,
        correlation_id=correlation_id,
        query_type=query_type,
        stage_models=stage_models,
        rag_attach=rag_attach,
        owner_hint=owner_hint,
        response_policy=body.response_policy,
        idempotency_key=body.idempotency_key,
        turn_budget=turn_budget,
    )
    bq = finish.bq
    if bq.raise_capability_unavailable:
        return (
            make_answer(
                body,
                answer="",
                sources=[],
                query_type=query_type,
                stage_accumulator=stage_models,
                fixed_response=True,
                business_query=bq.business_query,
            ),
            bq,
        )
    return (
        make_answer(
            body,
            answer=finish.answer_text,
            sources=finish.sources,
            query_type=query_type,
            completion_status=finish.completion_status,
            sql_stop_reason=finish.sql_stop_reason,
            sql_provenance=finish.sql_provenance,
            citations=finish.citations,
            stage_accumulator=stage_models,
            final_producer_purpose=finish.final_producer_purpose,
            business_query=bq.business_query,
        ),
        bq,
    )
