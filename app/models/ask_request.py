"""Request models for the /ask endpoint."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings
from app.models.correlation_id import validate_correlation_id
from app.models.page_context_v2 import PageContextV2
from app.models.record_context import RecordContext
from app.models.trusted_page_context import TrustedPageContext

AskOperation = Literal[
    "ask",
    "regenerate",
    "new_question",
    "clarification_reply",
    "result_page",
]
_MAX_EXCHANGE_ID_LEN = 64
_MAX_RUN_ID_LEN = 64


class Question(BaseModel):
    """Request body for the /ask endpoint."""

    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(
        default=None,
        min_length=1,
        max_length=2000,
        description="The question to ask about Multi Color Printers services",
        examples=["What paper sizes do you offer?"],
    )
    thread_id: str | None = Field(
        default=None,
        max_length=64,
        description="Opaque conversation id. Omit to start a new thread; echo back the "
        "thread_id from the previous Answer to continue the session.",
    )
    page_context: PageContextV2 | TrustedPageContext | None = Field(
        default=None,
        description="Laravel-resolved trusted page records for this ask.",
    )
    record_context: RecordContext | None = Field(
        default=None,
        description=(
            "Laravel Global Search results attached to this ask (digest-bound "
            "via the JWT record_context_digest claim; strictly separate from page_context, "
            "which carries the Jobs trusted-page contract instead)."
        ),
    )
    operation: AskOperation = Field(
        default="ask",
        description="ask for a new exchange; regenerate replaces the latest completed pair.",
    )
    target_exchange_id: str | None = Field(
        default=None,
        max_length=_MAX_EXCHANGE_ID_LEN,
        description="Required when operation=regenerate — opaque latest exchange id.",
    )
    run_id: str | None = Field(
        default=None,
        max_length=_MAX_RUN_ID_LEN,
        description="Opaque streaming run id for cooperative cancellation.",
    )
    continuation_token: str | None = Field(
        default=None,
        description="Optional continuation token for degraded / continuation turns.",
    )
    result_page_cursor: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Signed Business Query result-page cursor; separate from the "
            "degraded/clarification continuation_token."
        ),
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional client idempotency key.",
    )
    response_policy: Literal["allow_partial", "strict"] = Field(
        default="allow_partial",
        description="Whether a requested record-detail failure may return partial data.",
    )

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str | None) -> str | None:
        if value is None or not settings.strict_correlation_id:
            return value
        return validate_correlation_id(value)

    @model_validator(mode="after")
    def _validate_regenerate(self) -> Question:
        if (self.question is None) == (self.result_page_cursor is None):
            raise ValueError("exactly one of question or result_page_cursor is required")
        if self.result_page_cursor is not None and self.operation not in ("ask", "result_page"):
            raise ValueError("result_page_cursor is only valid for ask and result_page")
        if self.operation == "regenerate" and not self.target_exchange_id:
            raise ValueError("target_exchange_id is required when operation is regenerate")
        if self.operation in ("ask", "new_question") and self.target_exchange_id is not None:
            raise ValueError("target_exchange_id is only valid for regenerate")
        return self

    @model_validator(mode="before")
    @classmethod
    def _reject_client_policy_fields(cls, data: Any) -> Any:
        """Browsers must never send policy/tool fields — only Laravel trusted context."""
        if not isinstance(data, dict):
            return data
        forbidden = (
            "policy",
            "dispatch_mode",
            "tools",
            "capabilities",
            "page_context_proof",
            "context_mode",
            "page_context_digest",
            "record_context_digest",
        )
        for key in forbidden:
            if key in data:
                raise ValueError(f"unsupported field: {key}")
        ctx = data.get("page_context")
        if isinstance(ctx, dict):
            for key in (
                "policy",
                "dispatch_mode",
                "tools",
                "capabilities",
                "context_id",
                "context_mode",
                "page_context_digest",
            ):
                if key in ctx:
                    raise ValueError(f"unsupported page_context field: {key}")
        return data


class AskCancelRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=_MAX_RUN_ID_LEN)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> str:
        if not settings.strict_correlation_id:
            return value
        return validate_correlation_id(value)


from app.models.ask_v2_request import AskRequest, AskV2Request  # noqa: E402

__all__ = ["AskCancelRequest", "AskOperation", "AskRequest", "AskV2Request", "Question"]
