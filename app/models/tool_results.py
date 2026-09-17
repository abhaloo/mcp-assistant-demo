"""Whole-turn tool result wire models (spec §6, ADR 0076).

Pure wire types: every Ask transport (JSON, v1/v2 SSE, transcript) projects the
same frozen result, so the models live with the other wire models and import
nothing above them.
"""

from __future__ import annotations

from typing import Any, Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, model_validator

ToolName = Literal["business_query", "document_search"]

OmissionReason = Literal[
    "policy_denied",
    "circuit_open",
    "capacity_exhausted",
    "retriever_unavailable",
    "stage_timeout",
    "invalid_provenance",
    "output_limit",
    "clarification_required",
    "unsupported",
    "incomplete",
    "shadowed",
]

ComponentStatus = Literal[
    "succeeded",
    "denied",
    "unavailable",
    "timeout",
    "failed",
    "clarification_required",
    "unsupported",
    "incomplete",
    "shadowed",
]

TurnOutcomeType = Literal[
    "answered",
    "clarification_required",
    "unsupported",
    "incomplete",
    "denied",
    "shadowed",
]

Completeness = Literal["full", "partial", "none"]


class ToolOmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: ToolName
    reason: OmissionReason


class ComponentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: ToolName
    invocation_id: str
    status: ComponentStatus
    answer_query_ids: tuple[str, ...] = ()
    evidence_digest: str | None = None


class TurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    outcome_type: TurnOutcomeType
    completeness: Completeness
    trusted: bool
    selected: tuple[ToolName, ...]
    omissions: tuple[ToolOmission, ...]
    components: tuple[ComponentEvidence, ...]

    @model_validator(mode="after")
    def _validate_turn_result(self) -> Self:
        if self.completeness == "full" and len(self.omissions) > 0:
            raise ValueError(
                "Contradictory metadata: completeness cannot be 'full' with non-empty omissions"
            )
        if self.trusted and self.completeness in ("partial", "none"):
            raise ValueError(
                "Contradictory metadata: trusted cannot be True when completeness is "
                f"{self.completeness!r}"
            )
        return self


class ToolResultFields(TypedDict):
    """The closed set of additive fields every transport emits for a version 1 turn."""

    tool_result_version: Literal[1]
    completeness: Completeness
    omissions: list[dict[str, Any]]
    components: list[dict[str, Any]]
    restore_ref: str | None


def tool_result_fields(result: TurnResult, *, restore_ref: str | None = None) -> ToolResultFields:
    """Project a TurnResult into the additive fields shared by every transport."""
    return {
        "tool_result_version": 1,
        "completeness": result.completeness,
        "omissions": [o.model_dump(mode="json") for o in result.omissions],
        "components": [c.model_dump(mode="json") for c in result.components],
        "restore_ref": restore_ref,
    }
