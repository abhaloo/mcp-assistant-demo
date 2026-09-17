"""Transport-only SSE producer for Business Query Ask turns."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.auth import Principal
from app.conversation.turn import TurnContext
from app.core.turn_budget import TurnBudget
from app.models.schemas import Question
from app.models.tool_results import tool_result_fields
from app.providers.model_purpose import ModelPurpose
from app.query_records.bq_usage import fill_terminal_usage_from_bq
from app.query_records.context import TerminalUsageCapture
from app.resources import ProcessResources
from app.services.ask_frames import AskFrame, DataFrame, Disconnected
from app.services.ask_outcome import Answered, ResultPage, TurnEvidence, to_json_source
from app.services.business_query_page import produce_result_page_answer
from app.services.run_lifecycle import RunOutcome, StreamTerminal
from app.services.stream_transport import (
    chunk_answer_tokens,
    client_gone,
    commit_done,
    sse_done_payload,
    sse_producer_errors,
    sse_sources_from_answer_sources,
)
from app.telemetry.metrics import record_request

if TYPE_CHECKING:
    from app.providers.stage_model_report import StageModelAccumulator


async def produce_bq_stream(
    *,
    body: Question,
    principal: Principal,
    ctx: TurnContext,
    disconnected: Disconnected,
    queue: asyncio.Queue[AskFrame | None],
    terminal: StreamTerminal,
    run_id: str,
    query_type: str,
    stage_accumulator: StageModelAccumulator | None,
    answered: Answered,
    usage: TerminalUsageCapture | None = None,
) -> RunOutcome:
    """SSE renderer for an already-finished structured/both Ask outcome."""
    if stage_accumulator is None:
        raise TypeError("stage_accumulator is required")

    async with sse_producer_errors(terminal, query_type=query_type, role=principal.role) as box:
        if usage is not None and answered.bq is not None:
            fill_terminal_usage_from_bq(usage, answered.bq)
        answer_text = answered.answer_text
        sources = [to_json_source(src) for src in answered.sources]
        citations = answered.citations
        sql_provenance = answered.sql_provenance
        disambiguation = answered.disambiguation
        client_action = answered.client_action
        completion_status = answered.completion_status
        sql_stop_reason = answered.sql_stop_reason
        business_query = answered.business_query
        fulfillment_scope = answered.fulfillment_scope
        omitted_capabilities = answered.omitted_capabilities
        banner = answered.banner

        if await client_gone(disconnected, run_id):
            record_request(query_type=query_type, outcome="stopped")
            return "stopped"

        for piece in chunk_answer_tokens(answer_text):
            if await client_gone(disconnected, run_id):
                record_request(query_type=query_type, outcome="stopped")
                return "stopped"
            await queue.put(DataFrame("token", {"d": piece}))

        source_payload = sse_sources_from_answer_sources(sources) if sources else []
        await queue.put(DataFrame("sources", source_payload))
        await queue.put(DataFrame("citations", citations.model_dump()))

        if sql_provenance is not None:
            await queue.put(DataFrame("sql_provenance", sql_provenance.model_dump()))

        if disambiguation is not None:
            await queue.put(DataFrame("disambiguation", disambiguation.model_dump()))

        if client_action is not None:
            await queue.put(DataFrame("client_action", client_action.model_dump()))

        done_extra: dict = {"query_type": query_type}
        if fulfillment_scope is not None:
            done_extra["fulfillment_scope"] = fulfillment_scope
            if omitted_capabilities:
                done_extra["omitted_capabilities"] = list(omitted_capabilities)
            if banner is not None:
                done_extra["banner"] = banner
        if disambiguation is not None:
            done_extra["disambiguation"] = disambiguation.model_dump()
        if client_action is not None:
            done_extra["client_action"] = client_action.model_dump()
        if completion_status is not None:
            done_extra["completion_status"] = completion_status
        if sql_stop_reason is not None:
            done_extra["sql_stop_reason"] = sql_stop_reason
        if business_query is not None:
            done_extra["business_query"] = business_query.model_dump(mode="json")
        if answered.turn_result is not None:
            done_extra.update(tool_result_fields(answered.turn_result))

        outcome = await commit_done(
            body=body,
            principal=principal,
            ctx=ctx,
            disconnected=disconnected,
            terminal=terminal,
            answer=answer_text,
            done_payload=sse_done_payload(
                stage_accumulator,
                final_producer_purpose=ModelPurpose.record_reasoning,
                extra=done_extra,
            ),
            run_id=run_id,
            answer_query_type=query_type,
            bq=answered.bq,
            model_invoked=False,
            evidence=TurnEvidence.from_answered(answered, response_policy=body.response_policy),
        )
        if completion_status == "incomplete":
            record_request(
                query_type=query_type,
                outcome="incomplete" if outcome == "completed" else "stopped",
            )
        else:
            record_request(
                query_type=query_type,
                outcome="ok" if outcome == "completed" else "stopped",
            )
        box.value = outcome
    return box.value


async def produce_result_page_stream(
    *,
    body: Question,
    principal: Principal,
    resources: ProcessResources,
    ctx: TurnContext,
    disconnected: Disconnected,
    queue: asyncio.Queue[AskFrame | None],
    terminal: StreamTerminal,
    run_id: str,
    page: ResultPage | None = None,
    turn_budget: TurnBudget,
) -> RunOutcome:
    """SSE adapter for a signed stored-plan page; no classifier or planner path."""
    if page is not None:
        answer, bq = page.answer, page.bq
    else:
        answer, bq = await produce_result_page_answer(
            body,
            principal,
            resources=resources,
            lifecycle_run_id=run_id,
            turn_budget=turn_budget,
            progress=None,
        )
    for piece in chunk_answer_tokens(answer.answer):
        if await client_gone(disconnected, run_id):
            return "stopped"
        await queue.put(DataFrame("token", {"d": piece}))
    await queue.put(DataFrame("sources", []))
    await queue.put(DataFrame("citations", {"parsed": False, "cited": []}))
    done_extra: dict = {"query_type": "structured"}
    if bq.business_query is not None:
        done_extra["business_query"] = bq.business_query.model_dump(mode="json")
    commit_body = body.model_copy(update={"question": "[business query result page]"})
    return await commit_done(
        body=commit_body,
        principal=principal,
        ctx=ctx,
        disconnected=disconnected,
        terminal=terminal,
        answer=answer.answer,
        done_payload=sse_done_payload(
            None,
            fixed_response=True,
            extra=done_extra,
        ),
        run_id=run_id,
        answer_query_type="structured",
        bq=bq,
        model_invoked=False,
    )
