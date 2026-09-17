"""Domain contracts, actions, and observations for conversational coordinator."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.conversation.followup_contracts import FollowupFocus, SourceCandidate, SourceSelection

ActionKind = Literal["query_business", "search_documents", "explain_sources"]
ActionStatus = Literal["succeeded", "denied", "failed", "timed_out", "cancelled"]
QuestionOrigin = Literal["user", "model"]
StopReason = Literal[
    "finished",
    "clarified",
    "decision_limit",
    "action_limit",
    "business_query_limit",
    "document_search_limit",
    "action_not_allowed",
    "malformed_response",
    "explanation_unavailable",
    "context_budget_exceeded",
    "budget_expired",
    "cancelled",
    "action_protocol_error",
]


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class QueryBusiness(Action):
    model_config = ConfigDict(title="query_business", extra="forbid", frozen=True)
    kind: Literal["query_business"] = "query_business"
    question: str = Field(min_length=1, max_length=2000)


class SearchDocuments(Action):
    model_config = ConfigDict(title="search_documents", extra="forbid", frozen=True)
    kind: Literal["search_documents"] = "search_documents"
    question: str = Field(min_length=1, max_length=2000)


class ExplainSources(Action):
    model_config = ConfigDict(title="explain_sources", extra="forbid", frozen=True)
    kind: Literal["explain_sources"] = "explain_sources"
    selection: SourceSelection


class AnswerBlock(Action):
    text: str = Field(min_length=1, max_length=1600)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=8)
    value_refs: tuple[str, ...] = Field(default=(), max_length=16)
    claim_type: Literal["general", "evidence", "suggestion"]

    @model_validator(mode="after")
    def validate_claims_and_evidence(self) -> AnswerBlock:
        if self.claim_type == "evidence":
            if not self.evidence_ids:
                raise ValueError("evidence claim requires at least one evidence id")
        else:
            if self.evidence_ids:
                raise ValueError(
                    f"{self.claim_type} block cannot specify evidence ids; "
                    "unvalidated source ids forbidden"
                )
        return self


class FinishAnswer(Action):
    model_config = ConfigDict(title="finish_answer", extra="forbid", frozen=True)
    kind: Literal["finish_answer"] = "finish_answer"
    blocks: tuple[AnswerBlock, ...] = Field(min_length=1, max_length=8)


class Clarify(Action):
    model_config = ConfigDict(title="clarify", extra="forbid", frozen=True)
    kind: Literal["clarify"] = "clarify"
    question: str = Field(min_length=1, max_length=500)


CoordinatorAction = Annotated[
    QueryBusiness | SearchDocuments | ExplainSources | FinishAnswer | Clarify,
    Field(discriminator="kind"),
]


class HistoryLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    exchange_id: str
    user_text: str = Field(max_length=2000)


class CoordinatorContext(BaseModel):
    """What the model may see before any tool result: user text and handles, not cached answers."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    turn_id: str
    question: str = Field(min_length=1, max_length=2000)
    history: tuple[HistoryLine, ...] = Field(default=(), max_length=8)
    candidates: tuple[SourceCandidate, ...] = Field(default=(), max_length=8)
    focus: FollowupFocus = FollowupFocus()
    catalog_descriptions: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()


class ValueRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    label: str
    formatted: str


class Observation(BaseModel):
    """Business-safe projection of one terminal action outcome (R5 owns the projection)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str
    kind: ActionKind
    status: ActionStatus
    question_origin: QuestionOrigin | None = None
    evidence_ids: tuple[str, ...] = ()
    values: tuple[ValueRef, ...] = Field(default=(), max_length=16)
    summary: str = Field(default="", max_length=2000)
    truncated: bool = False
