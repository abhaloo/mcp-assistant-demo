"""Conversation transcript models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.conversation.reference_artifact import ReferenceArtifact
from app.models.tool_results import ComponentEvidence, ToolOmission
from app.services.record_intent import RecordAggregateContinuation

ContextMode = Literal["jobs", "none"]
TURN_CONTENT_MAX = 4000
_MAX_EXCHANGE_ID_LEN = 64


class BqTurnDigest(BaseModel):
    """Structured-turn summary for transcript history (no rows, no SQL)."""

    model_config = ConfigDict(extra="ignore")

    outcome: Literal["answered", "timeout"] = "answered"
    plan_fingerprint: str = Field(max_length=64)
    grain: Literal["scalar", "grouped", "entity_rows"]
    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    row_count: int = Field(ge=0)
    scalar_value: str | None = None
    receipt_title: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def _outcome_shape(self) -> BqTurnDigest:
        if self.outcome == "timeout":
            if (
                self.plan_fingerprint != ""
                or self.grain != "scalar"
                or self.row_count != 0
                or self.scalar_value is not None
            ):
                raise ValueError(
                    "timeout digest must use an empty fingerprint, scalar grain, and no rows"
                )
            return self
        if len(self.plan_fingerprint) != 64:
            raise ValueError("answered digest needs a 64-character plan fingerprint")
        return self


class TranscriptTurn(BaseModel):
    """One message in a server-side conversation transcript (not a full Q&A exchange)."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=TURN_CONTENT_MAX)
    exchange_id: str | None = Field(default=None, max_length=_MAX_EXCHANGE_ID_LEN)
    context_mode: ContextMode | None = Field(
        default=None,
        description="jobs when page context was active on the user turn; never structured data.",
    )
    reference_artifact: ReferenceArtifact | None = Field(default=None)
    aggregate_continuation: RecordAggregateContinuation | None = Field(default=None)
    bq_digest: BqTurnDigest | None = Field(default=None)
    tool_result_version: Literal[1] | None = Field(default=None)
    completeness: Literal["full", "partial", "none"] | None = Field(default=None)
    omissions: list[ToolOmission] = Field(default_factory=list)
    components: list[ComponentEvidence] = Field(default_factory=list)
    restore_ref: str | None = Field(default=None)
    answer_mode: Literal["explanation", "direct"] | None = None
    source_exchange_ids: tuple[str, ...] = ()
    conversation_subject: str | None = None

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
        if data.get("conversation_subject") is None:
            data.pop("conversation_subject", None)
        return data

    @model_validator(mode="after")
    def _assistant_only_state(self) -> TranscriptTurn:
        if self.aggregate_continuation is not None and self.role != "assistant":
            raise ValueError("aggregate_continuation is only valid on assistant turns")
        if self.bq_digest is not None and self.role != "assistant":
            raise ValueError("bq_digest is only valid on assistant turns")
        if self.tool_result_version is not None and self.role != "assistant":
            raise ValueError("tool_result_version is only valid on assistant turns")
        if self.answer_mode is not None and self.role != "assistant":
            raise ValueError("answer_mode is only valid on assistant turns")
        if self.source_exchange_ids and self.role != "assistant":
            raise ValueError("source_exchange_ids is only valid on assistant turns")
        if self.conversation_subject is not None and self.role != "assistant":
            raise ValueError("conversation_subject is only valid on assistant turns")
        return self
