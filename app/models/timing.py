"""Client-side timing models."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class QueryRecordTiming(BaseModel):
    trace_id: str = Field(min_length=1, max_length=128)
    feedback_token: str = Field(min_length=1, max_length=2048)
    ui_first_text_ms: int | None = Field(default=None, ge=0)
    completion_latency_ms: int | None = Field(default=None, ge=0)
    ui_first_activity_ms: int | None = Field(default=None, ge=0)
    ui_first_progress_ms: int | None = Field(default=None, ge=0)
    ui_first_row_ms: int | None = Field(default=None, ge=0)
    ui_completion_latency_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _normalize_timings(self) -> QueryRecordTiming:
        if self.ui_first_activity_ms is not None and self.ui_first_progress_ms is None:
            self.ui_first_progress_ms = self.ui_first_activity_ms
        elif self.ui_first_progress_ms is not None and self.ui_first_activity_ms is None:
            self.ui_first_activity_ms = self.ui_first_progress_ms

        if self.ui_first_text_ms is None:
            if self.ui_first_activity_ms is not None:
                self.ui_first_text_ms = self.ui_first_activity_ms
            elif self.ui_first_progress_ms is not None:
                self.ui_first_text_ms = self.ui_first_progress_ms

        if self.completion_latency_ms is None and self.ui_completion_latency_ms is not None:
            self.completion_latency_ms = self.ui_completion_latency_ms

        return self


class QueryRecordTimingResponse(BaseModel):
    stored: bool = Field(description="True when the Query Record row was updated.")
