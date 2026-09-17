"""Request model for the Ask AI v2 endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AskV2Operation = Literal["new_question", "regenerate", "clarification_reply", "result_page"]


class AskV2Request(BaseModel):
    """Canonical request payload for Ask AI protocol version 2."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["2"] = "2"
    operation: AskV2Operation
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    question: str | None = None
    continuation_ref: str | None = None
    result_page_cursor: str | None = None
    clarification_choice_id: str | None = None
    clarification_free_text: str | None = None
    idempotency_key: str = Field(min_length=1)
    deadline_at_ms: int = Field(gt=0)
    page_context: dict[str, Any] | None = None
    record_context: dict[str, Any] | None = None
    response_policy: str | None = "strict"

    @model_validator(mode="after")
    def _validate_operation_fields(self) -> AskV2Request:
        if self.operation == "new_question":
            if not self.question or not self.question.strip():
                raise ValueError("question is required when operation is new_question")
            if self.continuation_ref is not None:
                raise ValueError(
                    "continuation_ref is only valid for regenerate and clarification_reply"
                )
            if self.result_page_cursor is not None:
                raise ValueError("result_page_cursor is only valid for result_page")
            if self.clarification_choice_id is not None or self.clarification_free_text is not None:
                raise ValueError("clarification fields are only valid for clarification_reply")

        elif self.operation == "regenerate":
            if not self.continuation_ref or not self.continuation_ref.strip():
                raise ValueError("continuation_ref is required when operation is regenerate")
            if self.question is not None:
                raise ValueError("question is only valid for new_question")
            if self.result_page_cursor is not None:
                raise ValueError("result_page_cursor is only valid for result_page")
            if self.clarification_choice_id is not None or self.clarification_free_text is not None:
                raise ValueError("clarification fields are only valid for clarification_reply")

        elif self.operation == "clarification_reply":
            if not self.continuation_ref or not self.continuation_ref.strip():
                raise ValueError(
                    "continuation_ref is required when operation is clarification_reply"
                )
            if self.question is not None:
                raise ValueError("question is only valid for new_question")
            if self.result_page_cursor is not None:
                raise ValueError("result_page_cursor is only valid for result_page")
            has_choice = bool(self.clarification_choice_id and self.clarification_choice_id.strip())
            has_free_text = bool(
                self.clarification_free_text and self.clarification_free_text.strip()
            )
            if not (has_choice ^ has_free_text):
                raise ValueError(
                    "exactly one of clarification_choice_id or clarification_free_text "
                    "is required for clarification_reply"
                )

        elif self.operation == "result_page":
            if not self.result_page_cursor or not self.result_page_cursor.strip():
                raise ValueError("result_page_cursor is required when operation is result_page")
            if self.question is not None:
                raise ValueError("question is only valid for new_question")
            if self.continuation_ref is not None:
                raise ValueError(
                    "continuation_ref is only valid for regenerate and clarification_reply"
                )
            if self.clarification_choice_id is not None or self.clarification_free_text is not None:
                raise ValueError("clarification fields are only valid for clarification_reply")

        return self


# Canonical Ask aliases
AskRequest = AskV2Request
AskOperation = AskV2Operation
