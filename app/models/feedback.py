"""User feedback request and response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

FeedbackReason = Literal[
    "incorrect",
    "incomplete",
    "irrelevant",
    "unsafe",
    "other",
]


class Feedback(BaseModel):
    """User verdict on a prior answer, keyed by its trace_id (the LangSmith root run id)."""

    trace_id: str = Field(min_length=1, max_length=128)
    feedback_token: str = Field(min_length=1, max_length=2048)
    verdict: Literal["up", "down"]
    reason: FeedbackReason | None = Field(
        default=None,
        description="Required for thumbs-down; omitted for thumbs-up.",
    )
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_reason(self) -> Feedback:
        if self.verdict == "down" and self.reason is None:
            raise ValueError("reason is required when verdict is down")
        return self


class FeedbackResponse(BaseModel):
    recorded: bool = Field(
        description=(
            "True when feedback was durably stored on the Query Record row "
            "and/or accepted by LangSmith."
        )
    )
