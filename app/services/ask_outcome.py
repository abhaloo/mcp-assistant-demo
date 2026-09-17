"""Transport-neutral ask result: one union, two source projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.business_query.outcomes import BusinessQueryWireOutcome
from app.business_query.plan import BusinessQueryPlan
from app.conversation.turn import TurnContext
from app.models.ask_response import Answer, QueryType
from app.models.ask_v2_events import FollowUpOffer
from app.models.citations import CitationsPayload
from app.models.result_presentation import ResultPresentation
from app.models.sql_provenance import SqlProvenance
from app.models.tool_results import TurnResult
from app.providers.model_purpose import ModelPurpose
from app.providers.stage_model_report import StageModelAccumulator
from app.query_records.context import TerminalUsageCapture
from app.rag.retrieval.document_contracts import DocumentProvenance
from app.rag.retrieval.passages import (
    RetrievedSource,
    filter_retrieved,
    retrieved_from_docs,
    retrieved_from_sources,
    sources_from_docs,
    to_json_source,
    to_sse_source,
)
from app.services.ask_result_projection import (
    BranchResult,
    GuardError,
    GuardOutcome,
    guarded_call,
)
from app.services.business_query_service import AskBusinessQueryResult
from app.services.record_intent import RecordAggregateContinuation
from app.services.retained_evidence import RetainedBqMember

__all__ = [
    "Answered",
    "AskOutcome",
    "BranchResult",
    "CapabilityUnavailable",
    "FixedMessage",
    "GuardError",
    "GuardOutcome",
    "Replayed",
    "ResultPage",
    "RetrievedSource",
    "Stopped",
    "TurnEvidence",
    "filter_retrieved",
    "guarded_call",
    "retrieved_from_docs",
    "retrieved_from_sources",
    "sources_from_docs",
    "to_json_source",
    "to_sse_source",
]


@dataclass(frozen=True)
class Answered:
    answer_text: str
    sources: tuple[RetrievedSource, ...]
    query_type: QueryType
    citations: CitationsPayload
    stage_models: StageModelAccumulator
    final_producer_purpose: ModelPurpose
    ctx: TurnContext | None = None
    sql_provenance: SqlProvenance | None = None
    completion_status: Literal["complete", "incomplete"] | None = None
    sql_stop_reason: str | None = None
    business_query: BusinessQueryWireOutcome | None = None
    bq: AskBusinessQueryResult | None = None
    disambiguation: object = None
    client_action: object = None
    follow_up_suggestions: tuple[str, ...] | None = None
    fulfillment_scope: Literal["full", "structured_only"] | None = None
    omitted_capabilities: tuple[str, ...] = ()
    banner: str | None = None
    usage: TerminalUsageCapture | None = None
    continuation_request_id: str | None = None
    presentation: ResultPresentation | None = None
    follow_up_offer: FollowUpOffer | None = None
    turn_result: TurnResult | None = None
    answer_mode: Literal["explanation", "direct"] | None = None
    source_exchange_ids: tuple[str, ...] = ()
    source_restore_refs: tuple[str, ...] = ()
    document_provenance: tuple[DocumentProvenance, ...] = ()
    retained_members: tuple[RetainedBqMember, ...] = ()


@dataclass(frozen=True)
class TurnEvidence:
    """What a version 1 turn retains for an authorized restore (spec §7)."""

    turn_result: TurnResult
    answer_text: str
    sources: tuple[RetrievedSource, ...]
    citations: CitationsPayload
    business_query: BusinessQueryWireOutcome | None
    presentation: ResultPresentation | None
    plan: BusinessQueryPlan | None
    scope_fingerprint: str | None
    response_policy: Literal["allow_partial", "strict"]
    source_restore_refs: tuple[str, ...] = ()
    retained_members: tuple[RetainedBqMember, ...] = ()
    document_provenance: tuple[DocumentProvenance, ...] = ()

    @classmethod
    def from_answered(
        cls, answered: Answered, *, response_policy: Literal["allow_partial", "strict"]
    ) -> TurnEvidence | None:
        if answered.turn_result is None:
            return None
        retained: tuple[RetainedBqMember, ...] = ()
        if getattr(answered, "retained_members", ()):
            retained = answered.retained_members
        elif answered.bq is not None and getattr(answered.bq, "retained_members", ()):
            retained = answered.bq.retained_members

        doc_prov: tuple[DocumentProvenance, ...] = getattr(answered, "document_provenance", ())

        return cls(
            turn_result=answered.turn_result,
            answer_text=answered.answer_text,
            sources=answered.sources,
            citations=answered.citations,
            business_query=answered.business_query,
            presentation=answered.presentation,
            plan=answered.bq.plan if answered.bq is not None else None,
            scope_fingerprint=answered.bq.scope_fingerprint if answered.bq is not None else None,
            response_policy=response_policy,
            source_restore_refs=getattr(answered, "source_restore_refs", ()),
            retained_members=retained,
            document_provenance=doc_prov,
        )


@dataclass(frozen=True)
class FixedMessage:
    message: str
    query_type: QueryType
    stage_models: StageModelAccumulator
    ctx: TurnContext | None = None
    continuation_token: str | None = None
    omitted_capabilities: tuple[str, ...] = ()
    banner: str | None = None
    aggregate_continuation: RecordAggregateContinuation | None = None
    turn_result: TurnResult | None = None


@dataclass(frozen=True)
class Replayed:
    answer: Answer
    query_type: QueryType
    stage_models: StageModelAccumulator
    ctx: TurnContext | None = None


@dataclass(frozen=True)
class ResultPage:
    answer: Answer
    bq: AskBusinessQueryResult
    ctx: TurnContext | None = None


@dataclass(frozen=True)
class CapabilityUnavailable:
    detail: str
    answer: Answer | None
    bq: AskBusinessQueryResult | None
    ctx: TurnContext | None = None
    turn_result: TurnResult | None = None


@dataclass(frozen=True)
class Stopped:
    query_type: QueryType
    stage_models: StageModelAccumulator
    ctx: TurnContext | None = None
    usage: TerminalUsageCapture | None = None


AskOutcome = Answered | FixedMessage | Replayed | ResultPage | CapabilityUnavailable | Stopped
