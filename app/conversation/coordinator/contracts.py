"""Domain contracts, actions, and observations for conversational coordinator."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.business_query.plan.plan_diff import PlanDigest
from app.business_query.plan.plan_patch import PlanPatch
from app.business_query.wire.failure_note import FailureNote
from app.conversation.followup_contracts import (
    MAX_SOURCE_CANDIDATES,
    FollowupFocus,
    SourceCandidate,
    SourceSelection,
)

MAX_HISTORY_EXCHANGES = (
    20  # The exchanges the coordinator reads: the forty messages the panel keeps.
)

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


# The longest note_to_person the panel shows; the prompt rule states the same number.
NOTE_MAX_CHARS = 200


class Decision(Action):
    """A coordinator decision: an action with an optional note for the person."""

    note_to_person: str | None = Field(
        default=None,
        description=(
            "One or two short sentences to the person, in the first person and in their own terms: "
            f"what you will do next and why. At most {NOTE_MAX_CHARS} characters. "
            "No markdown, no tool or field names, no ids."
        ),
    )


class RecordBinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    resource: str = Field(min_length=1)
    member: str = Field(min_length=1)
    value: str = Field(min_length=1, max_length=128)


class BusinessQuestion(BaseModel):
    """The person's words and the coordinator's reading of them, together."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    raw: str = Field(min_length=1, max_length=2000)
    reading: str = Field(min_length=1, max_length=2000)
    binding: RecordBinding | None = None


class QueryBusiness(Decision):
    model_config = ConfigDict(title="query_business", extra="forbid", frozen=True)
    kind: Literal["query_business"] = "query_business"
    question: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "The person's question with its references resolved: which record, which "
            "period, which earlier answer. Never add a field, an amount or a date the "
            "person did not ask for, except a record's own status and dates when the "
            "person asks why that record has or lacks something."
        ),
    )
    continues: PlanPatch | None = Field(
        default=None,
        description=(
            "Patch the last business answer instead of planning a new query. "
            "Use when the person asks to add, drop, sort, limit, or filter the "
            "same result. Name columns in the person's words."
        ),
    )
    binding: RecordBinding | None = Field(
        default=None,
        description=(
            "The record the conversation already identified: resource, the "
            "member whose value the person quoted (for example invoice.invoice_number), "
            "and that value. Only when the value appeared in an earlier answer or the "
            "person's words. Never a date, a month or an amount: a period or an amount "
            "belongs in the question."
        ),
    )
    compound: bool = Field(
        default=False,
        description=(
            "True when the second thing the person asks for depends on the answer to "
            "the first; then the question holds only the first thing."
        ),
    )


class SearchDocuments(Decision):
    model_config = ConfigDict(title="search_documents", extra="forbid", frozen=True)
    kind: Literal["search_documents"] = "search_documents"
    question: str = Field(min_length=1, max_length=2000)


class ExplainSources(Decision):
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


class FinishAnswer(Decision):
    model_config = ConfigDict(title="finish_answer", extra="forbid", frozen=True)
    kind: Literal["finish_answer"] = "finish_answer"
    blocks: tuple[AnswerBlock, ...] = Field(min_length=1, max_length=8)
    unanswered_part: str | None = Field(
        default=None,
        min_length=2,
        max_length=160,
        description=(
            "When the question asked for two things and only the first could be answered, "
            "the second thing written as the person's next question. Never the rest of a "
            "list a table already shows."
        ),
    )


# The wire continuation of a card the coordinator itself asked for (no query ran).
COORDINATOR_CLARIFY = "coordinator_clarify"

# A clarify offers this many choices at least and at most, each a short label.
MIN_CLARIFY_CHOICES = 2
MAX_CLARIFY_CHOICES = 4
MAX_CLARIFY_CHOICE_CHARS = 80
# The card adds its own "Something else" with a text box, so a catch-all choice repeats it.
_CATCH_ALL_PREFIXES = ("something else", "anything else")


class Clarify(Decision):
    model_config = ConfigDict(title="clarify", extra="forbid", frozen=True)
    kind: Literal["clarify"] = "clarify"
    question: str = Field(min_length=1, max_length=500)
    choices: tuple[str, ...] = Field(
        default=(),
        description="Two to four short options the person can pick. Name a record by its number.",
    )

    @field_validator("choices", mode="before")
    @classmethod
    def _tidy_choices(cls, value: object) -> tuple[str, ...]:
        """Keep two to four distinct labels of at most 80 characters, no catch-all;
        otherwise none."""
        if not isinstance(value, (list, tuple)):
            return ()
        kept: list[str] = []
        for item in value:
            label = item.strip()[:MAX_CLARIFY_CHOICE_CHARS] if isinstance(item, str) else ""
            catch_all = label.lower().startswith(_CATCH_ALL_PREFIXES)
            if label and not catch_all and label not in kept:
                kept.append(label)
        return tuple(kept[:MAX_CLARIFY_CHOICES]) if len(kept) >= MIN_CLARIFY_CHOICES else ()


CoordinatorAction = Annotated[
    QueryBusiness | SearchDocuments | ExplainSources | FinishAnswer | Clarify,
    Field(discriminator="kind"),
]


class ShownMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    member: str
    values: tuple[str, ...]


class PageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    resource: str
    record_id: str


class HistoryLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    exchange_id: str
    user_text: str = Field(max_length=2000)
    assistant_text: str = Field(default="", max_length=600)
    summary: str | None = None
    shown: tuple[ShownMember, ...] = ()
    facts: tuple[str, ...] = Field(default=(), max_length=8)
    documents: tuple[str, ...] = Field(default=(), max_length=5)
    choice: tuple[str, str] | None = None
    page_scope: Literal["current"] | None = None
    digest: PlanDigest | None = None


class ClarificationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str
    choice_id: str | None = None
    label: str | None = None
    free_text: str | None = None

    @model_validator(mode="after")
    def validate_label_or_free_text(self) -> ClarificationSelection:
        has_label = self.label is not None
        has_free_text = self.free_text is not None
        if has_label == has_free_text:
            raise ValueError("ClarificationSelection requires exactly one of label or free_text")
        return self


class CoordinatorContext(BaseModel):
    """What the model may see before any tool result: user text and handles, not cached answers."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    turn_id: str
    question: str = Field(min_length=1, max_length=2000)
    history: tuple[HistoryLine, ...] = Field(default=(), max_length=MAX_HISTORY_EXCHANGES)
    candidates: tuple[SourceCandidate, ...] = Field(default=(), max_length=MAX_SOURCE_CANDIDATES)
    focus: FollowupFocus = FollowupFocus()
    catalog_descriptions: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()
    # Record types (manifest resource keys) this person cannot see.
    unreachable: tuple[str, ...] = ()
    page_scope: Literal["current", "all"] = "all"
    page_record: PageRecord | None = None
    selection: ClarificationSelection | None = None


class ValueRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    label: str
    formatted: str


class ObservedColumn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    kind: str
    identifier: bool = False
    label: str | None = None


class ObservedTable(BaseModel):
    """One table of a business answer: its heading, and how many of the
    observation's rows, in order, are its own."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    heading: str = Field(max_length=600)
    rows: int = Field(ge=0)
    record_counts: dict[str, str | int | None] | None = None


ObservationKind = ActionKind | Literal["decision_invalid"]

# Every decision_invalid observation starts with this line; the prompt prints it once.
REPAIR_PREFIX = "Your last reply was not a valid action:"


class Observation(BaseModel):
    """Business-safe projection of one terminal action outcome (R5 owns the projection)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    action_id: str
    kind: ObservationKind
    status: ActionStatus
    question_origin: QuestionOrigin | None = None
    evidence_ids: tuple[str, ...] = ()
    values: tuple[ValueRef, ...] = Field(default=(), max_length=16)
    columns: tuple[ObservedColumn, ...] = ()
    rows: tuple[dict[str, str | int | float | bool | None], ...] = Field(default=(), max_length=20)
    row_identity: dict[str, str | int | None] | None = None
    tables: tuple[ObservedTable, ...] = Field(default=(), max_length=4)
    failure: FailureNote | None = None
    summary: str = Field(default="", max_length=2000)
    truncated: bool = False
    page_keys: tuple[str, ...] = ()
    page_labels: tuple[str, ...] = ()
    # The restored answers that stood on document passages. A restore does not carry
    # their passages, so a draft that restates one needs a document search first.
    document_answers: tuple[str, ...] = ()
