"""One finish policy for Business Query JSON and SSE (ADR 0054)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.auth import Principal
from app.business_query.definitions import bundle_for_manifest
from app.business_query.wire.explainer import explain_business_query_plan
from app.business_query.wire.module import BusinessProgressSink, BusinessQueryOwnerHint
from app.conversation.turn import TurnContext
from app.core.ask_errors import resolve_production_route
from app.core.breakers import retriever_breaker
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.guardrails.audit import emit_bq_pii_audit_for_answered
from app.models.result_presentation import ResultPresentation
from app.models.schemas import CitationsPayload, Source, SqlProvenance
from app.providers.model_purpose import ModelPurpose
from app.providers.stage_model_report import StageModelAccumulator
from app.rag.retrieval.document_contracts import DocumentFailure
from app.rag.retrieval.passages import to_json_source
from app.resources import ProcessResources, current_process_resources
from app.services.ask_outcome import BranchResult, GuardOutcome
from app.services.business_query_service import run_business_query_for_ask
from app.services.client_action_rules import derive_client_action
from app.services.tool_composition import serving_document_executor
from app.telemetry import emit_circuit_breaker_reject

if TYPE_CHECKING:
    from app.services.business_query_service import AskBusinessQueryResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BqFinish:
    bq: AskBusinessQueryResult
    answer_text: str
    sources: list[Source]
    sql_provenance: SqlProvenance | None
    completion_status: Literal["complete", "incomplete"] | None
    sql_stop_reason: str | None
    final_producer_purpose: ModelPurpose
    citations: CitationsPayload
    disambiguation: Any = None
    client_action: Any = None
    continuation: str | None = None
    prompt: str | None = None
    choices: list[Any] | tuple[Any, ...] | None = None
    allow_free_text: bool = True
    presentation: ResultPresentation | None = None


def build_guarded_rag_attach(
    ctx: TurnContext,
    access_tiers: list[str],
    config: dict,
    *,
    principal: Principal,
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
) -> Callable[[], Awaitable[GuardOutcome]]:
    """Retrieval-only RAG attach as the verified caller. No generation.

    ``access_tiers`` and ``config`` stay in the signature for existing callers;
    the executor resolves tiers from the principal.
    """
    del access_tiers, config

    async def rag_attach() -> GuardOutcome:
        adapter = serving_document_executor(current_process_resources())
        try:
            result = await adapter.execute(
                query=ctx.search_query,
                principal=principal,
                budget=turn_budget,
            )
        except DeadlineExpiredError:
            # Evidence only: a deadline here leaves the committed BQ answer standing.
            return GuardOutcome("rag", None, "retriever_unavailable", False)
        if isinstance(result, DocumentFailure):
            if result.code == "circuit_open":
                emit_circuit_breaker_reject(
                    breaker_name=retriever_breaker.name, state=retriever_breaker.state.value
                )
                return GuardOutcome("rag", None, "circuit_open", True)
            return GuardOutcome("rag", None, "retriever_unavailable", False)
        sources = [to_json_source(passage) for passage in result.passages]
        return GuardOutcome("rag", BranchResult(answer="", sources=sources), None, False)

    return rag_attach


async def finish_bq_policy(
    *,
    question: str,
    principal: Principal,
    resources: ProcessResources,
    correlation_id: str,
    query_type: str,
    stage_models: StageModelAccumulator,
    rag_attach: Callable[[], Awaitable[GuardOutcome]] | None = None,
    progress: BusinessProgressSink | None = None,
    owner_hint: BusinessQueryOwnerHint | None = None,
    response_policy: Literal["allow_partial", "strict"] = "allow_partial",
    idempotency_key: str | None = None,
    continuation: str | None = None,
    clarification_reply: str | None = None,
    clarification_prompt: str | None = None,
    history: tuple[tuple[str, str], ...] = (),
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
) -> BqFinish:
    """Map a BQ turn to transport-neutral finish fields for JSON and SSE."""
    if stage_models is None:
        raise TypeError("stage_models is required")

    bq = await run_business_query_for_ask(
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

    # Audit is observability: it must never fail a good answer. The gate is
    # `bq.plan is not None`, not the disposition -- shadow mode still runs the
    # renderer over real rows before it discards the answer text, so the
    # exposure happened and must be recorded. Fail-open: a bundle lookup miss
    # or lineage-sidecar error is logged and the turn still ships.
    if bq.plan is not None and principal.manifest_hash:
        try:
            bundle = bundle_for_manifest(principal.manifest_hash)
            emit_bq_pii_audit_for_answered(
                correlation_id,
                disposition=bq.disposition,
                plan=bq.plan,
                bundle=bundle,
            )
        except Exception:
            logger.warning(
                "bq pii audit failed (fail-open, answer unaffected) correlation_id=%s",
                correlation_id,
                exc_info=True,
            )

    empty = BqFinish(
        bq=bq,
        answer_text="",
        sources=[],
        sql_provenance=None,
        completion_status=None,
        sql_stop_reason=None,
        final_producer_purpose=ModelPurpose.record_reasoning,
        citations=CitationsPayload(parsed=False),
    )
    if bq.raise_capability_unavailable:
        return empty

    stage_models.record_used(resolve_production_route(ModelPurpose.record_reasoning))

    # "both" answers from Business Query only. RAG runs for its SOURCES so the
    # user still sees supporting documents; its generated text is discarded --
    # there is no merge step. Retrieval-only also keeps the extra model call off
    # the turn.
    sources: list[Source] = []
    attach_eligible = (
        query_type == "both"
        and rag_attach is not None
        and bq.disposition == "answered"
        and bq.completion_status == "complete"
        and bq.sql_stop_reason is None
        and bool(bq.answer_text.strip())
        and bq.answer_text != "I wasn't able to answer that from the records I can look up."
    )
    if attach_eligible:
        rag_outcome = await rag_attach()
        if rag_outcome.result is not None:
            sources = list(rag_outcome.result.sources)

    explanation = None
    if bq.plan is not None:
        try:
            matched_count = len(bq.record_links) if bq.record_links else 0
            explanation = explain_business_query_plan(
                bq.plan,
                matched_records_count=matched_count,
            )
        except Exception:
            logger.warning(
                "ast explainer failed (fail-open, explanation omitted) correlation_id=%s",
                correlation_id,
                exc_info=True,
            )
            explanation = None

    sql_provenance = None
    if bq.record_links or explanation is not None:
        sql_provenance = SqlProvenance(
            queries=[],
            record_links=bq.record_links,
            explanation=explanation,
        )

    client_action = None
    try:
        client_action = derive_client_action(
            question,
            plan=bq.plan,
            record_links=bq.record_links,
        )
    except Exception:
        logger.warning(
            "client_action derivation failed (fail-open, omitted) correlation_id=%s",
            correlation_id,
            exc_info=True,
        )
        client_action = None

    presentation = None
    if bq.disposition == "answered" and bq.business_query is not None:
        first = bq.business_query.envelopes[0] if bq.business_query.envelopes else None
        presentation = first.presentation if first is not None else None

    continuation = None
    prompt = None
    choices = None
    allow_free_text = True
    if bq.business_query and isinstance(bq.business_query, dict):
        continuation = bq.business_query.get("continuation")
        prompt = bq.business_query.get("prompt")
        choices = bq.business_query.get("choices")
        allow_free_text = bq.business_query.get("allow_free_text", True)

    return BqFinish(
        bq=bq,
        answer_text=bq.answer_text,
        sources=sources,
        sql_provenance=sql_provenance,
        completion_status=bq.completion_status,
        sql_stop_reason=bq.sql_stop_reason,
        final_producer_purpose=ModelPurpose.record_reasoning,
        citations=CitationsPayload(parsed=False),
        disambiguation=bq.disambiguation,
        client_action=client_action,
        continuation=continuation,
        prompt=prompt,
        choices=choices,
        allow_free_text=allow_free_text,
        presentation=presentation,
    )
