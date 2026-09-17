"""Single Answer construction site for every ask query path."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from app.business_query.outcomes import BusinessQueryWireOutcome
from app.config import settings
from app.models.ask_v2_events import FollowUpOffer
from app.models.result_presentation import ResultPresentation
from app.models.schemas import Answer, CitationsPayload, QueryType, Question, Source, SqlProvenance
from app.providers.model_purpose import ModelPurpose
from app.providers.stage_model_report import (
    FIXED_RESPONSE_MODEL_SENTINEL,
    StageModelAccumulator,
)


def make_answer(
    body: Question,
    *,
    answer: str,
    sources: list[Source],
    query_type: QueryType,
    sql_provenance: SqlProvenance | None = None,
    follow_up_suggestions: list[str] | None = None,
    citations: CitationsPayload | None = None,
    completion_status: Literal["complete", "incomplete"] | None = None,
    sql_stop_reason: str | None = None,
    stage_accumulator: StageModelAccumulator | None = None,
    final_producer_purpose: ModelPurpose | None = None,
    fixed_response: bool = False,
    fulfillment_scope: str | None = None,
    omitted_capabilities: list[str] | None = None,
    banner: str | None = None,
    continuation_token: str | None = None,
    business_query: BusinessQueryWireOutcome | None = None,
    presentation: ResultPresentation | None = None,
    follow_up_offer: FollowUpOffer | None = None,
    answer_mode: Literal["explanation", "direct"] | None = None,
    source_exchange_ids: Sequence[str] = (),
) -> Answer:
    """Single Answer construction site for all query paths."""
    if fixed_response:
        model = FIXED_RESPONSE_MODEL_SENTINEL
    elif stage_accumulator is not None and final_producer_purpose is not None:
        model = stage_accumulator.producer_model(purpose=final_producer_purpose)
    else:
        model = settings.active_chat_model
    stage_models = stage_accumulator.build_report() if stage_accumulator is not None else None
    return Answer(
        question=body.question or "",
        answer=answer,
        sources=sources,
        model=model,
        query_type=query_type,
        sql_provenance=sql_provenance,
        follow_up_suggestions=follow_up_suggestions or [],
        citations=citations or CitationsPayload(parsed=False),
        completion_status=completion_status,
        sql_stop_reason=sql_stop_reason,
        stage_models=stage_models,
        fulfillment_scope=fulfillment_scope,
        omitted_capabilities=omitted_capabilities or [],
        banner=banner,
        continuation_token=continuation_token,
        business_query=business_query,
        presentation=presentation,
        follow_up_offer=follow_up_offer,
        answer_mode=answer_mode,
        source_exchange_ids=list(source_exchange_ids),
    )
