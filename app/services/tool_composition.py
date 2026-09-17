"""Bind domain handlers to the typed tool catalog."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from app.auth import Principal
from app.business_query.outcomes import Incomplete
from app.business_query.ports import BusinessProgressSink
from app.business_query.seal.evidence import BusinessQueryEvidenceContext
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.business_query.wire.module import BusinessQueryModule
from app.business_query.wire.request_lifecycle import BqRequestScope
from app.config import settings
from app.core.breakers import retriever_breaker
from app.core.turn_budget import TurnBudget
from app.rag.chains.document_chain import retrieve_and_redact
from app.rag.retrieval.document_contracts import DocumentFailure, DocumentSearchResult
from app.rag.retrieval.document_execution import DocumentExecutor
from app.rag.retrieval.document_policy import DocumentExecutionPolicy
from app.rag.retrieval.retriever_factory import get_document_retriever
from app.resources import ProcessResources
from app.services.business_query_mapping import map_outcome
from app.tools.catalog import ToolCatalog
from app.tools.contracts import (
    BusinessQueryHandler,
    BusinessQueryInvocation,
    DocumentHandler,
    DocumentSearchInput,
    ToolContext,
)
from app.tools.turn_contracts import BqTurnPort


def bind_tool_catalog(
    *,
    bq_handler: BusinessQueryHandler,
    document_handler: DocumentHandler,
) -> ToolCatalog:
    return ToolCatalog(bq_handler=bq_handler, document_handler=document_handler)


@dataclass(frozen=True)
class FoundationBqExecution:
    module: BusinessQueryModule
    scope: BqRequestScope
    progress: BusinessProgressSink | None
    evidence: BusinessQueryEvidenceContext | None
    turn_budget: TurnBudget


def build_foundation_bq_handler(execution: FoundationBqExecution) -> BusinessQueryHandler:
    async def handler(
        call: BusinessQueryInvocation, ctx: ToolContext
    ) -> CommittedBqResult | AskBusinessQueryResult:
        planned_set = call.arguments.to_planned_query_set()
        outcome = await execution.module.execute_planned_set(
            execution.scope.request,
            planned_set,
            progress=execution.progress,
            evidence=execution.evidence,
            trace=execution.scope.trace,
            turn_budget=execution.turn_budget,
        )
        execution.scope.finish(outcome)
        return map_outcome(outcome, shadow=False, evidence_sealed=False)

    return handler


def build_production_bq_handler(operation: BqTurnPort) -> BusinessQueryHandler:
    async def handler(
        call: BusinessQueryInvocation, ctx: ToolContext
    ) -> CommittedBqResult | AskBusinessQueryResult:
        if hasattr(operation, "request") and operation.request is not None:
            if ctx.principal != operation.request.principal:
                return map_outcome(
                    Incomplete(reason_code="adapter_invalid"), shadow=False, evidence_sealed=False
                )
            if ctx.correlation_id != operation.request.correlation_id:
                return map_outcome(
                    Incomplete(reason_code="adapter_invalid"), shadow=False, evidence_sealed=False
                )
        ctx.budget.check_not_expired()
        return await operation.execute(call)

    return handler


def bind_document_retrieve() -> Callable[[str, list[str]], list[Document]]:
    """Two-argument retrieve port on the bounded document store; collection
    and top_k are server owned."""

    def retrieve(query: str, tiers: list[str]) -> list[Document]:
        return retrieve_and_redact(
            query,
            access_tiers=tiers,
            collection_name=settings.collection_name,
            top_k=settings.top_k,
            store=get_document_retriever(settings.collection_name),
        )

    return retrieve


def serving_document_executor(resources: ProcessResources) -> DocumentExecutor:
    """The one DocumentExecutor Ask serves with: bounded store, process
    executor, the retriever breaker, and the frozen policy."""
    return DocumentExecutor(
        bind_document_retrieve(),
        executor=resources.document_executor,
        policy=DocumentExecutionPolicy(fault=settings.ask_document_fault),
        breaker=retriever_breaker,
        enabled=True,
    )


async def empty_document_handler(
    payload: DocumentSearchInput, ctx: ToolContext
) -> DocumentSearchResult | DocumentFailure:
    del payload, ctx
    return DocumentSearchResult(passages=(), provenance=(), truncated=False)


def build_document_handler(adapter: DocumentExecutor) -> DocumentHandler:
    async def handler(
        payload: DocumentSearchInput, ctx: ToolContext
    ) -> DocumentSearchResult | DocumentFailure:
        return await adapter.execute(
            query=payload.query,
            principal=ctx.principal,
            budget=ctx.budget,
        )

    return handler


_DEFAULT_D1_PROFILE_PATH = Path("docs/superpowers/reports/2026-09-13-document-tool-profile.json")
_STAGE_CEILING_S = 5.0
_MAX_PASSAGE_BYTES = 32_768
_MAX_RESULT_BYTES = 65_536


def _profile_within_limits(profile: dict[str, Any]) -> bool:
    limits = profile.get("candidate_limits", {})
    stage_ceiling = limits.get("stage_ceiling_seconds", _STAGE_CEILING_S)
    passage_cap = limits.get("max_passage_bytes", _MAX_PASSAGE_BYTES)
    result_cap = limits.get("max_result_bytes", _MAX_RESULT_BYTES)
    if stage_ceiling > _STAGE_CEILING_S:
        return False
    for group in profile.get("concurrency_profiles", []):
        cases = group.get("cases", [])
        if group.get("exceeds_limits") is True:
            return False
        if any(
            case.get("exceeds_limits") is True
            or case.get("wall_time_seconds", 0) > stage_ceiling
            or case.get("passage_text_bytes", 0) > passage_cap
            or case.get("result_bytes", 0) > result_cap
            for case in cases
        ):
            return False
    return True


def check_tool_layer_enablement(
    principal: Principal,
    *,
    profile_path: Path | str | None = None,
) -> bool:
    """Fail-closed composition gate for the versioned tool layer (spec §8).

    All of: the server flag, the signed ``tool_result_version=1`` claim, and the
    accepted document-tool profile on disk matching its recorded SHA-256 with
    every measured case inside the candidate limits. Anything unreadable or
    unrecorded keeps the legacy path.
    """
    recorded = settings.ask_tool_layer_profile_sha256.strip().lower()
    if not settings.ask_tool_layer_enabled or principal.tool_result_version != 1 or not recorded:
        return False
    path = Path(profile_path) if profile_path is not None else _DEFAULT_D1_PROFILE_PATH
    try:
        raw = path.read_bytes()
        profile = json.loads(raw)
    except (OSError, ValueError):
        return False
    if hashlib.sha256(raw).hexdigest() != recorded or not isinstance(profile, dict):
        return False
    return _profile_within_limits(profile)


is_tool_layer_enabled = check_tool_layer_enablement
