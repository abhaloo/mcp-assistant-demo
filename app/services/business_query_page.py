"""Ask JSON adapter for signed Business Query result-page continuations."""

from __future__ import annotations

from app.auth import Principal
from app.business_query.ports import BusinessProgressSink
from app.config import settings
from app.core.ask_errors import CAPABILITY_UNAVAILABLE_MESSAGE
from app.core.errors import CapabilityUnavailableError
from app.core.turn_budget import TurnBudget
from app.models.schemas import Answer, Question
from app.resources import ProcessResources
from app.services.business_query_service import (
    AskBusinessQueryResult,
    compose_business_query_page_answer,
)


async def produce_result_page_answer(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    lifecycle_run_id: str | None,
    turn_budget: TurnBudget,
    progress: BusinessProgressSink | None = None,
) -> tuple[Answer, AskBusinessQueryResult]:
    """Reload, reauthorize, and execute a stored plan without Ask preparation."""
    if settings.business_query_mode != "enabled" or not settings.query_record_database_url.strip():
        raise CapabilityUnavailableError(CAPABILITY_UNAVAILABLE_MESSAGE)
    correlation_id = lifecycle_run_id or body.run_id or "result-page"
    bq = await compose_business_query_page_answer(
        resources=resources,
        page_cursor=body.result_page_cursor,
        principal=principal,
        correlation_id=correlation_id,
        max_rows=20,
        idempotency_key=body.idempotency_key,
        turn_budget=turn_budget,
        progress=progress,
    )
    from app.services.ask_service import make_answer

    answer = make_answer(
        body.model_copy(update={"question": ""}),
        answer=bq.answer_text,
        sources=[],
        query_type="structured",
        completion_status=bq.completion_status,
        sql_stop_reason=bq.sql_stop_reason,
        business_query=bq.business_query,
    )
    return answer, bq
