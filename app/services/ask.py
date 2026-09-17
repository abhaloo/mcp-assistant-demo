"""One ask ladder. Both transports render the outcome it returns."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import langsmith as ls
from langchain_core.documents import Document
from opentelemetry.trace import Span

from app.auth import Principal
from app.config import settings
from app.conversation.turn import TurnContext
from app.core.ask_errors import (
    resolve_production_route,
)
from app.core.breakers import retriever_breaker
from app.core.errors import (
    CircuitOpenError,
    ResultPageDisabledError,
    ServiceUnavailableError,
)
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.models.ask_response import Answer, QueryType
from app.models.citations import CitationsPayload
from app.models.schemas import Question
from app.models.sql_provenance import SqlProvenance
from app.providers.model_purpose import ModelPurpose
from app.rag.chains.document_chain import (
    RagTurnInput,
)
from app.rag.retrieval.document_contracts import DocumentFailure, DocumentSearchResult
from app.rag.retrieval.passages import documents_from_passages, retrieved_from_docs
from app.resources import ProcessResources, current_process_resources
from app.services.ask_frames import AskStage, Disconnected
from app.services.ask_outcome import (
    Answered,
    AskOutcome,
    FixedMessage,
    Replayed,
    ResultPage,
    Stopped,
)
from app.services.ask_prepare import (
    ContinuationClaimed,
    DispatchClassified,
    PreparedAsk,
    PreparedCoordinatorTurn,
    PreparedFixedMessage,
    PreparedTurn,
    RecordsOnly,
    ReplayCompleted,
    prepare_ask,
)
from app.services.ask_structured import structured_outcome
from app.services.business_query_page import produce_result_page_answer
from app.services.records_only import generate_records_only_answer
from app.services.semantic_answer import semantic_answer
from app.services.stream_transport import client_gone
from app.services.tool_composition import build_document_handler, serving_document_executor
from app.telemetry import emit_circuit_breaker_reject
from app.tools.contracts import DocumentSearchInput, ToolContext


class ProgressSink(Protocol):
    def stage(self, stage: AskStage) -> None: ...


async def _still_connected() -> bool:
    return False


def _as_str_tuple(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list | tuple) else ()


async def ask(
    body: Question,
    principal: Principal,
    *,
    resources: ProcessResources,
    progress: ProgressSink | None = None,
    disconnected: Disconnected | None = None,
    lifecycle_run_id: str | None = None,
    on_classified: Callable[[Span, QueryType], None] | None = None,
    prepared: PreparedAsk | None = None,
    turn_budget: TurnBudget,
) -> AskOutcome:
    if disconnected is None:
        disconnected = _still_connected
    if body.result_page_cursor is not None:
        if not settings.ask_result_page_enabled:
            raise ResultPageDisabledError("Paging through results is not available in Ask AI.")
        answer, bq = await produce_result_page_answer(
            body,
            principal,
            resources=resources,
            lifecycle_run_id=lifecycle_run_id,
            turn_budget=turn_budget,
            progress=progress,
        )
        return ResultPage(
            answer=answer,
            bq=bq,
            ctx=TurnContext(
                thread_id=body.thread_id,
                history=[],
                search_query="[business query result page]",
                original_question=body.question or "[business query result page]",
            ),
        )
    if prepared is None:
        prepared = await prepare_ask(
            body,
            principal,
            resources=resources,
            on_classified=on_classified,
            turn_budget=turn_budget,
        )
    match prepared:
        case PreparedCoordinatorTurn() as coord_turn:
            from app.services.ask_coordinator import run_coordinator_ask

            return await run_coordinator_ask(
                coord_turn,
                body=body,
                principal=principal,
                resources=resources,
                progress=progress,
                disconnected=disconnected,
                lifecycle_run_id=lifecycle_run_id,
                turn_budget=turn_budget,
            )
        case ReplayCompleted(turn=turn, answer=stored):
            return Replayed(
                answer=Answer.model_validate(stored),
                query_type="structured",
                stage_models=turn.stage_models,
                ctx=turn.ctx,
            )
        case PreparedFixedMessage() as fixed:
            return FixedMessage(
                message=fixed.message,
                query_type=fixed.query_type,
                stage_models=fixed.turn.stage_models,
                ctx=fixed.turn.ctx,
                continuation_token=fixed.continuation_token,
                omitted_capabilities=fixed.omitted_capabilities,
                banner=fixed.banner,
            )
        case RecordsOnly(turn=turn):
            ctx = turn.ctx
            stage_models = turn.stage_models
            assert ctx.page_context is not None
            assert body.question is not None
            stage_models.record_used(resolve_production_route(ModelPurpose.record_reasoning))
            # Records-only prompts and outputs contain structured page data.
            # Keep the root lifecycle trace, but suppress all model child runs.
            with ls.tracing_context(enabled=False):
                result = await generate_records_only_answer(
                    body.question, ctx.page_context, ctx.history
                )
            links = result.record_links if isinstance(result.record_links, list) else []
            return Answered(
                answer_text=result.answer,
                sources=(),
                query_type="structured",
                citations=CitationsPayload(parsed=False),
                stage_models=stage_models,
                final_producer_purpose=ModelPurpose.record_reasoning,
                ctx=ctx,
                sql_provenance=SqlProvenance(queries=[], record_links=links),
                follow_up_suggestions=_as_str_tuple(result.follow_up_suggestions),
            )
        case ContinuationClaimed(
            turn=turn,
            continuation_request_id=continuation_request_id,
            omitted_capabilities=omitted_capabilities,
            banner=banner,
        ):
            return await structured_outcome(
                body,
                principal,
                resources=resources,
                turn=turn,
                query_type="structured",
                progress=progress,
                lifecycle_run_id=lifecycle_run_id,
                fulfillment_scope="structured_only",
                omitted_capabilities=omitted_capabilities or ("document_search",),
                banner=banner,
                continuation_request_id=continuation_request_id,
                turn_budget=turn_budget,
            )
        case DispatchClassified(turn=turn, query_type=query_type) if query_type in (
            "structured",
            "both",
        ):
            return await structured_outcome(
                body,
                principal,
                resources=resources,
                turn=turn,
                query_type=query_type,
                progress=progress,
                lifecycle_run_id=lifecycle_run_id,
                turn_budget=turn_budget,
            )
        case DispatchClassified(turn=turn, query_type="semantic"):
            return await _semantic_outcome(
                body,
                principal,
                turn=turn,
                progress=progress,
                disconnected=disconnected,
                lifecycle_run_id=lifecycle_run_id,
                resources=resources,
                turn_budget=turn_budget,
            )
        case _:
            raise AssertionError(f"unhandled prepared ask: {type(prepared)!r}")


async def _document_search(
    query: str,
    *,
    principal: Principal,
    turn_budget: TurnBudget | None = None,
    resources: ProcessResources | None = None,
) -> DocumentSearchResult:
    """Search documents as the verified caller; failures become Ask errors."""
    owned = resources or current_process_resources()
    handler = build_document_handler(serving_document_executor(owned))
    result = await handler(
        DocumentSearchInput(query=query),
        ToolContext(
            principal=principal,
            correlation_id="",
            budget=turn_budget or UNBOUNDED_BUDGET,
            record_context=None,
            origin="ask",
        ),
    )
    if isinstance(result, DocumentFailure):
        if result.code == "circuit_open":
            emit_circuit_breaker_reject(
                breaker_name=retriever_breaker.name, state=retriever_breaker.state.value
            )
            raise CircuitOpenError("rag circuit open")
        raise ServiceUnavailableError(f"rag unavailable: {result.code}")
    return result


async def _retrieve_documents(  # noqa: PLR0913
    turn_input: RagTurnInput,
    access_tiers: list[str],
    config: dict,
    *,
    principal: Principal,
    turn_budget: TurnBudget | None = None,
    resources: ProcessResources | None = None,
) -> list[Document]:
    """Retrieve as the verified caller. ``access_tiers`` and ``config`` stay in
    the signature for existing test doubles; the executor resolves tiers from
    the principal."""
    del access_tiers, config
    result = await _document_search(
        turn_input.search_query,
        principal=principal,
        turn_budget=turn_budget,
        resources=resources,
    )
    return documents_from_passages(result.passages)


async def _semantic_outcome(
    body: Question,
    principal: Principal,
    *,
    turn: PreparedTurn,
    progress: ProgressSink | None,
    disconnected: Disconnected,
    lifecycle_run_id: str | None,
    resources: ProcessResources | None = None,
    turn_budget: TurnBudget | None = None,
) -> AskOutcome:
    if await client_gone(disconnected, lifecycle_run_id):
        return Stopped(query_type="semantic", stage_models=turn.stage_models, ctx=turn.ctx)
    if progress is not None:
        progress.stage("searching_documents")
    docs = await _retrieve_documents(
        turn.ctx.to_rag_input(),
        turn.access_tiers,
        {},
        principal=principal,
        turn_budget=turn_budget,
        resources=resources,
    )
    document_result = DocumentSearchResult(
        passages=retrieved_from_docs(docs),
        provenance=(),
        truncated=False,
    )
    return await semantic_answer(
        body,
        principal,
        turn=turn,
        progress=progress,
        disconnected=disconnected,
        lifecycle_run_id=lifecycle_run_id,
        document_result=document_result,
    )
