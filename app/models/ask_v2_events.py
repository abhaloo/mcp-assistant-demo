"""Event models for Ask AI protocol version 2 streaming response."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.business_query.outcomes import BusinessQueryWireOutcome, RecordDetail
from app.models.citations import CitationsPayload, Source
from app.models.result_presentation import ResultPresentation
from app.models.sql_provenance import QueryExplanation
from app.models.tool_results import ComponentEvidence, ToolOmission

# Text on the stream is either prose a person should read or the deterministic
# row serialization a consumer with a typed table can hide. An older consumer
# ignores the field and keeps a readable answer.
TextContentKind = Literal["narrative", "table_fallback"]


TableColumnRole = Literal["current", "previous", "delta", "delta_pct"]


class TableColumn(BaseModel):
    """Column definition for streamed table output."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value_type: str = Field(min_length=1)
    currency_key: str | None = None
    href_template: str | None = None
    role: TableColumnRole | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_optional_fields(self, serializer):
        data = serializer(self)
        if data.get("href_template") is None:
            data.pop("href_template", None)
        if data.get("role") is None:
            data.pop("role", None)
        return data


class InteractionOption(BaseModel):
    """Discrete option for client interaction or clarification."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    # Optional subtitle: the choice rewrite or value_prompt when the stall
    # card carries one. Resolver candidates omit it.
    detail: str | None = None


class AskV2EventBase(BaseModel):
    """Common envelope fields for Ask AI v2 streaming events."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["2"] = "2"
    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)


# The stages the engine reports. A closed vocabulary here means no free text
# can reach the panel through an activity, whatever produced it.
ActivityKind = Literal[
    "understand",
    "planning",
    "thinking",
    "authorize",
    "authorized",
    "finding_record",
    "querying",
    "sealing",
    "answering",
    "searching_documents",
    "reading_page",
    # The previous server reports its one planning step under this name.
    "plan_search",
]
ActivityState = Literal["running", "completed", "failed"]


class FollowUpAction(BaseModel):
    """One optional next question the answer model offered. The panel shows
    ``label`` and sends ``prompt`` as a new question; the id is the dispatch key."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^fu-[a-z0-9-]{1,48}$")
    label: str = Field(min_length=2, max_length=80)
    prompt: str = Field(min_length=2, max_length=160)


class FollowUpOffer(BaseModel):
    """The whole offer from one ``offer_follow_up`` call. One invalid action
    rejects the offer."""

    model_config = ConfigDict(extra="forbid")

    actions: list[FollowUpAction] = Field(min_length=1, max_length=3)


class ActivityEvent(AskV2EventBase):
    """Progress/activity indicator for an ongoing operation."""

    event_type: Literal["activity"] = "activity"
    activity_id: str = Field(min_length=1)
    activity_kind: ActivityKind
    tool_kind: str = Field(min_length=1)
    state: ActivityState
    elapsed_ms: int = Field(ge=0)
    # Present only when the turn has more than one sub-plan.
    ordinal: int | None = Field(default=None, ge=0)
    of: int | None = Field(default=None, ge=1)
    subject: str | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_ordinals(self, serializer):
        data = serializer(self)
        if data.get("ordinal") is None:
            data.pop("ordinal", None)
        if data.get("of") is None:
            data.pop("of", None)
        if data.get("subject") is None:
            data.pop("subject", None)
        return data


class TextDeltaEvent(AskV2EventBase):
    """Incremental text generation delta."""

    event_type: Literal["text_delta"] = "text_delta"
    delta: str
    content_kind: TextContentKind = "narrative"


class ThoughtStartEvent(AskV2EventBase):
    """Model reasoning has started for one timeline step. ``activity_id`` is
    the step the transcript nests under; each thinking or planning step
    carries its own phase."""

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["thought_start"] = "thought_start"
    activity_id: str = Field(min_length=1)


class ThoughtDeltaEvent(AskV2EventBase):
    """Incremental chunk of model reasoning for the step named by ``activity_id``."""

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["thought_delta"] = "thought_delta"
    activity_id: str = Field(min_length=1)
    delta: str


class ThoughtDoneEvent(AskV2EventBase):
    """Model reasoning for the step named by ``activity_id`` has completed."""

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["thought_done"] = "thought_done"
    activity_id: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)


class ResultStreamAuthorizedEvent(AskV2EventBase):
    """Signal that result rows/stream have passed policy authorization."""

    event_type: Literal["result_stream_authorized"] = "result_stream_authorized"


class TableStartEvent(AskV2EventBase):
    """Header and column schema for a streamed tabular result."""

    event_type: Literal["table_start"] = "table_start"
    table_id: str = Field(min_length=1)
    columns: list[TableColumn]
    presentation: ResultPresentation | None = None


class TableRowsEvent(AskV2EventBase):
    """Batch of typed rows for an active table stream."""

    event_type: Literal["table_rows"] = "table_rows"
    table_id: str = Field(min_length=1)
    rows: list[dict[str, Any]]


class TableEndEvent(AskV2EventBase):
    """Completion marker and integrity digest for a table stream."""

    event_type: Literal["table_end"] = "table_end"
    table_id: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    table_digest: str = Field(min_length=1)
    # Executor fact. ``row_count`` is what was streamed; this is how many matched.
    total_row_count: int | None = Field(default=None, ge=0)


class RecordDetailsEvent(AskV2EventBase):
    """Authorized detail facts for one answer, streamed ahead of the terminal frame.

    Each detail names its owner, so a consumer rebinds it to the record the
    terminal envelope lists; that envelope carries no details itself."""

    event_type: Literal["record_details"] = "record_details"
    answer_query_id: str | None = None
    record_details: list[RecordDetail]


class InteractionEvent(AskV2EventBase):
    """Clarification or human-in-the-loop interaction prompt."""

    event_type: Literal["interaction"] = "interaction"
    interaction_kind: str = Field(min_length=1)
    continuation_ref: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    options: list[InteractionOption]
    free_text_allowed: bool


class CitationSetEvent(AskV2EventBase):
    """Document sources and citation bindings emitted ahead of terminal."""

    event_type: Literal["citation_set"] = "citation_set"
    sources: list[Source] = Field(default_factory=list)
    citations: CitationsPayload = Field(default_factory=lambda: CitationsPayload(parsed=False))

    @model_validator(mode="before")
    @classmethod
    def _unwrap_payload(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "run_id" not in data:
                data["run_id"] = "envelope"
            if "sequence" not in data:
                data["sequence"] = 0
            if "payload" in data:
                payload = data.pop("payload")
                if isinstance(payload, dict):
                    if "sources" in payload and "sources" not in data:
                        data["sources"] = payload["sources"]
                    if "citations" in payload and "citations" not in data:
                        data["citations"] = payload["citations"]
        return data


class TurnOutcomeEvent(AskV2EventBase):
    """Terminal turn outcome with evidence and trusted answer attribution."""

    event_type: Literal["turn_outcome"] = "turn_outcome"
    outcome_type: str = Field(min_length=1)
    trusted: bool
    answer_query_id: str | None = None
    evidence_digest: str | None = None
    query_record_ref: str | None = None
    # A refusal states why. ``reason_code`` is the closed operator vocabulary
    # the module already records; ``message`` is the copy shown to the person
    # who asked. Both stay unset on an answered turn.
    reason_code: str | None = None
    message: str | None = None
    # Echoed from the request so the panel can keep the conversation.
    thread_id: str | None = None
    # Server-measured turn time. The browser clock is provisional until this
    # arrives.
    duration_ms: int | None = Field(default=None, ge=0)
    follow_ups: list[FollowUpAction] = Field(default_factory=list, max_length=3)
    # The deterministic reading of the sealed plan: subject, filters, period.
    # Produced by the explainer from plan literals, never by a model, and it
    # carries no SQL.
    explanation: QueryExplanation | None = None
    # The sealed result envelope, which is what lets the panel show a support
    # reference and a trust drawer. It carries answer text, authorized rows and
    # hashes only -- no SQL, prompt, reasoning or tool arguments -- and only a
    # terminal event may carry it.
    business_query: BusinessQueryWireOutcome | None = None

    # Version 1 tool result fields (additive per Spec §6)
    tool_result_version: Literal[1] | None = None
    completeness: Literal["full", "partial", "none"] | None = None
    omissions: list[ToolOmission] = Field(default_factory=list)
    components: list[ComponentEvidence] = Field(default_factory=list)
    restore_ref: str | None = None
    answer_mode: Literal["explanation", "direct"] | None = None
    source_exchange_ids: list[str] = Field(default_factory=list, max_length=8)

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


class StreamErrorEvent(AskV2EventBase):
    """Terminal stream error with error code and retry policy."""

    event_type: Literal["stream_error"] = "stream_error"
    code: str = Field(min_length=1)
    retryable: bool
    # Copy for the panel. A stream error without one renders as an unnamed
    # failure, which reads the same to a person whatever went wrong.
    message: str | None = None


AskV2Event = Annotated[
    ActivityEvent
    | TextDeltaEvent
    | ThoughtStartEvent
    | ThoughtDeltaEvent
    | ThoughtDoneEvent
    | ResultStreamAuthorizedEvent
    | TableStartEvent
    | TableRowsEvent
    | TableEndEvent
    | RecordDetailsEvent
    | InteractionEvent
    | CitationSetEvent
    | TurnOutcomeEvent
    | StreamErrorEvent,
    Field(discriminator="event_type"),
]

# Canonical Ask event aliases
AskEventBase = AskV2EventBase
AskEvent = AskV2Event
