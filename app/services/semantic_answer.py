"""Semantic generation from a typed document-search result."""

from __future__ import annotations

import langsmith as ls

from app.auth import Principal
from app.concurrency import llm_slot
from app.config import settings
from app.core.ask_errors import resolve_production_route
from app.models.record_context import RecordContext
from app.models.schemas import Question
from app.prompts.registry import registry
from app.providers.model_purpose import ModelPurpose
from app.query_records.context import TerminalUsageCapture
from app.rag.chains.document_chain import (
    _select_prompt,
    generation_prompt_for_rag_turn,
    get_rag_continuation_runnable,
    get_rag_generation_runnable,
)
from app.rag.citations import finalize_citations
from app.rag.retrieval.document_contracts import DocumentSearchResult
from app.rag.retrieval.passages import documents_from_passages
from app.services.ask_frames import Disconnected
from app.services.ask_outcome import (
    Answered,
    AskOutcome,
    RetrievedSource,
    Stopped,
    filter_retrieved,
    to_json_source,
)
from app.services.ask_prepare import PreparedTurn, prompt_pipeline_for
from app.services.follow_up_offer import (
    FollowUpCapture,
    accept_for_principal,
    continuation_messages,
    needs_continuation,
    offer_follow_up_tool,
)
from app.services.stream_transport import client_gone
from app.telemetry.context import current_query_id
from app.telemetry.helpers import cost_status_for_usage, set_gen_ai_usage
from app.telemetry.metrics import record_citation_finalization, record_token_usage
from app.telemetry.spans import llm_span


def _token_delta(chunk: object) -> str:
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b
            if isinstance(b, str)
            else str(b.get("text", ""))
            if isinstance(b, dict) and b.get("type") == "text"
            else ""
            for b in content
        ]
        return "".join(parts)
    return str(content) if content else ""


def _retrieved_from_record_context(record_context: RecordContext) -> tuple[RetrievedSource, ...]:
    return tuple(
        RetrievedSource(
            id=f"record:{r.resource_type}:{r.record_id}",
            content="; ".join(f"{k}: {v}" for k, v in r.fields.items()),
            source_file=f"record:{r.resource_type}:{r.record_id}",
            title=r.label,
            resource_type=r.resource_type,
            record_id=r.record_id,
            label=r.label,
            link_key=r.link_key,
        )
        for r in record_context.records
    )


async def semantic_answer(  # noqa: PLR0913
    body: Question,
    principal: Principal,
    *,
    turn: PreparedTurn,
    progress: object | None,
    disconnected: Disconnected,
    lifecycle_run_id: str | None,
    document_result: DocumentSearchResult,
) -> AskOutcome:
    """Generate a semantic answer from retrieved passages. Does not retrieve."""
    ctx = turn.ctx
    stage_models = turn.stage_models
    usage = TerminalUsageCapture()
    input_tokens = output_tokens = reasoning_tokens = cost_status = None

    def _capture_usage() -> None:
        usage.input_tokens = input_tokens
        usage.output_tokens = output_tokens
        usage.reasoning_tokens = reasoning_tokens
        usage.cost_status = cost_status

    async def _stopped() -> Stopped:
        _capture_usage()
        return Stopped(query_type="semantic", stage_models=stage_models, ctx=ctx, usage=usage)

    if await client_gone(disconnected, lifecycle_run_id):
        return await _stopped()
    stage_models.record_used(resolve_production_route(ModelPurpose.rag_answer))
    turn_input = ctx.to_rag_input()
    prompt_version = registry.version(prompt_pipeline_for("semantic"))
    run_tree = ls.get_current_run_tree()
    if run_tree is not None:
        run_tree.metadata.update(
            {
                "query_id": current_query_id(),
                "query_type": "semantic",
                "role": principal.role,
                "prompt_version": prompt_version,
            }
        )
    config = {
        "metadata": {
            "role": principal.role,
            "query_type": "semantic",
            "access_tiers": turn.access_tiers,
            "prompt_version": prompt_version,
        }
    }
    docs = documents_from_passages(document_result.passages)
    offers_enabled = settings.ask_follow_up_offer_enabled
    capture = FollowUpCapture()
    generation = get_rag_generation_runnable(
        tools=[offer_follow_up_tool()] if offers_enabled else None
    )
    prompt_vars = generation_prompt_for_rag_turn(turn_input, docs)
    streamed_answer_parts: list[str] = []

    async def consume(stream) -> bool:
        """Drains one model stream into the answer parts; False when the client left."""
        nonlocal input_tokens, output_tokens, reasoning_tokens
        async for chunk in stream:
            if await client_gone(disconnected, lifecycle_run_id):
                return False
            text = _token_delta(chunk)
            if text:
                streamed_answer_parts.append(text)
            capture.record_chunk(chunk)
            chunk_usage = set_gen_ai_usage(span, chunk)
            if chunk_usage is not None:
                input_tokens, output_tokens = chunk_usage
            meta = getattr(chunk, "usage_metadata", None) or {}
            reasoning_val = (meta.get("output_token_details") or {}).get("reasoning")
            if reasoning_val is not None:
                reasoning_tokens = int(reasoning_val)
        return True

    follow_up_offer = None
    with llm_span(operation="generation") as span:
        async with llm_slot():
            if not await consume(generation.astream(prompt_vars, config)):
                span.set_attribute("gen_ai.cost_status", "partial")
                cost_status = "partial"
                return await _stopped()
            follow_up_offer = (
                accept_for_principal(capture, question=ctx.search_query, principal=principal)
                if offers_enabled
                else None
            )
            # A tool-only response is not an answer yet; the model gets its
            # result and finishes.
            if needs_continuation(capture, streamed_answer_parts):
                messages = continuation_messages(
                    _select_prompt(prompt_vars), capture, accepted=follow_up_offer is not None
                )
                if not await consume(get_rag_continuation_runnable().astream(messages, config)):
                    span.set_attribute("gen_ai.cost_status", "partial")
                    cost_status = "partial"
                    return await _stopped()
        cost_status = cost_status_for_usage(input_tokens, output_tokens)
        span.set_attribute("gen_ai.cost_status", cost_status)
        _capture_usage()
    if await client_gone(disconnected, lifecycle_run_id):
        return await _stopped()
    retrieved = document_result.passages
    full_answer = "".join(streamed_answer_parts)
    finalization = finalize_citations(full_answer, [to_json_source(src) for src in retrieved])
    record_citation_finalization(
        invalid_markers=finalization.invalid_markers,
        unbound_markers=finalization.unbound_markers,
        missing_bindings=finalization.missing_bindings,
        repair_attempted=finalization.repair_attempted,
    )
    retrieved = filter_retrieved(retrieved, finalization.citations)
    if ctx.record_context is not None:
        retrieved = _retrieved_from_record_context(ctx.record_context) + retrieved
    producer_model = stage_models.producer_model(purpose=ModelPurpose.rag_answer)
    if input_tokens is not None and output_tokens is not None:
        record_token_usage(
            operation="generation",
            model=producer_model or settings.active_chat_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    return Answered(
        answer_text=finalization.answer,
        sources=retrieved,
        query_type="semantic",
        citations=finalization.citations,
        stage_models=stage_models,
        final_producer_purpose=ModelPurpose.rag_answer,
        ctx=ctx,
        usage=usage,
        follow_up_offer=follow_up_offer,
    )
