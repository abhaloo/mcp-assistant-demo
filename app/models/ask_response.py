"""Response models for the /ask endpoint."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_serializer

from app.business_query.outcomes import BusinessQueryWireOutcome
from app.models.ask_v2_events import FollowUpOffer
from app.models.citations import CitationsPayload, Source
from app.models.client_directives import ClientAction, DisambiguationPayload
from app.models.result_presentation import ResultPresentation
from app.models.sql_provenance import SqlProvenance
from app.models.tool_results import ComponentEvidence, ToolOmission, TurnResult
from app.providers.stage_model_report import StageModelReport

QueryType = Literal["semantic", "structured", "both"]
FollowUpSuggestion = Annotated[str, Field(min_length=2, max_length=160)]


class Answer(BaseModel):
    """Response body from the /ask endpoint."""

    question: str = Field(description="The original question")
    answer: str = Field(description="The generated answer")
    sources: list[Source] = Field(description="Source chunks used to generate the answer")
    model: str = Field(description="LLM model used for generation")
    query_type: QueryType = Field(
        default="semantic",
        description=(
            "How the question was answered: 'semantic' (docs), 'structured' (SQL), or 'both'"
        ),
    )
    trace_id: str | None = Field(
        default=None,
        description="LangSmith root run id for this answer; null when tracing is off.",
    )
    feedback_token: str | None = Field(
        default=None,
        description="Short-lived signed token authorizing feedback for trace_id.",
    )
    thread_id: str | None = Field(
        default=None,
        description="Conversation thread id for this answer; null when chat sessions are off.",
    )
    exchange_id: str | None = Field(
        default=None,
        description="Opaque exchange id for regenerate targeting.",
    )
    sql_provenance: SqlProvenance | None = Field(
        default=None,
        description="Executed SQL + record links for structured/both answers",
    )
    follow_up_suggestions: list[FollowUpSuggestion] = Field(
        default_factory=list,
        max_length=3,
        description="Short, answer-specific prompts the user can ask next.",
    )
    citations: CitationsPayload = Field(
        default_factory=lambda: CitationsPayload(parsed=False),
        description="Validated document citation bindings for this answer.",
    )
    completion_status: Literal["complete", "incomplete"] | None = Field(
        default=None,
        description=(
            "Optional completion signal: 'incomplete' when a SQL run budget/stall "
            "stopped before a final answer; null/omitted for normal answers."
        ),
    )
    sql_stop_reason: str | None = Field(
        default=None,
        description=(
            "Budget/stall reason when completion_status is incomplete (e.g. max_llm_calls)."
        ),
    )
    stage_models: StageModelReport | None = Field(
        default=None,
        description="Additive per-purpose model route report for this answer.",
    )
    client_action: ClientAction | None = Field(
        default=None,
        description="Optional UI action instruction (e.g. highlight element or switch tab)",
    )
    disambiguation: DisambiguationPayload | None = Field(
        default=None,
        description="Structured disambiguation choices when query entity is ambiguous",
    )
    fulfillment_scope: Literal["full", "structured_only"] | None = Field(
        default=None,
        description="Fulfillment mode: full, or structured_only when document search is omitted",
    )
    omitted_capabilities: list[str] = Field(
        default_factory=list,
        description="List of capability names omitted during response generation",
    )
    banner: str | None = Field(
        default=None,
        description="User-visible warning banner when answering under degraded capabilities",
    )
    continuation_token: str | None = Field(
        default=None,
        description="Signed token to confirm continuation when capability is omitted",
    )
    business_query: BusinessQueryWireOutcome | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Canonical Business Query outcome/envelope shared by JSON and SSE",
    )
    presentation: ResultPresentation | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Server-owned receipt title and filter facts for a structured result",
    )
    follow_up_offer: FollowUpOffer | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Accepted optional next questions offered by the answer model",
    )
    tool_result_version: Literal[1] | None = Field(
        default=None,
        description="Version of tool result protocol (1 when versioned tool layer is active)",
    )
    completeness: Literal["full", "partial", "none"] | None = Field(
        default=None,
        description="Whole-turn completeness: full, partial, or none",
    )
    omissions: list[ToolOmission] = Field(
        default_factory=list,
        description="List of omitted tool capabilities and reasons",
    )
    components: list[ComponentEvidence] = Field(
        default_factory=list,
        description="Evidence and status per invoked tool component",
    )
    restore_ref: str | None = Field(
        default=None,
        description="Opaque server reference for authorized evidence restore",
    )
    answer_mode: Literal["explanation", "direct"] | None = Field(
        default=None,
        description=(
            "Delivery mode: 'explanation' for source-only narrative, "
            "'direct' for general response without query, null/omitted for data answers"
        ),
    )
    source_exchange_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Referenced prior exchange ids for explanation answers",
    )
    # In-process handoff to the SSE projections; never serialized.
    turn_result: TurnResult | None = Field(default=None, exclude=True)

    @model_serializer(mode="wrap")
    def _omit_unset_tool_results(self, serializer):
        data = serializer(self)
        if data.get("tool_result_version") is None:
            data.pop("tool_result_version", None)
            data.pop("completeness", None)
            data.pop("omissions", None)
            data.pop("components", None)
            data.pop("restore_ref", None)
        if data.get("answer_mode") is None:
            data.pop("answer_mode", None)
        if not data.get("source_exchange_ids"):
            data.pop("source_exchange_ids", None)
        return data

    @field_validator("follow_up_suggestions")
    @classmethod
    def _unique_follow_up_suggestions(cls, value: list[str]) -> list[str]:
        suggestions: list[str] = []
        seen: set[str] = set()
        for raw in value:
            suggestion = " ".join(raw.split())
            key = suggestion.casefold()
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(suggestion)
        return suggestions


class AskCancelResponse(BaseModel):
    cancelled: bool = Field(description="True when a live run accepted the cancel signal.")
