from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    position: int = Field(ge=1, le=8)
    exchange_id: str
    restore_ref: str
    grain: Literal["scalar", "grouped", "entity_rows", "documents", "mixed"]
    user_question: str = Field(max_length=160)


class SourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_positions: tuple[int, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid_positions(self) -> "SourceSelection":
        values = self.source_positions
        if len(set(values)) != len(values) or any(p < 1 or p > 8 for p in values):
            raise ValueError("source positions must be unique and between 1 and 8")
        return self


class FollowupFocus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["none", "resolved", "direct", "unavailable"] = "none"
    source_positions: tuple[int, ...] = ()
    subject_question: str | None = None

    @model_validator(mode="after")
    def valid_focus(self) -> "FollowupFocus":
        if self.status == "resolved":
            SourceSelection(source_positions=self.source_positions)
        elif self.source_positions:
            raise ValueError("only resolved focus has source positions")
        if (self.status == "direct") != bool(self.subject_question):
            raise ValueError("only direct focus requires a subject")
        return self
