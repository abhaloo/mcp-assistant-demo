"""Request models, protocol sinks, and adapter contracts for wire orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth import Principal
from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Answered
from app.business_query.plan import BusinessQueryPlan


class BusinessQueryOwnerHint(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: str
    record_id: int | str
    binding_member: str | None = None


class BusinessQueryRequest(BaseModel):
    """Caller request — no backend, SQL, tables, or authority-bearing continuation.

    Closed shapes (Wave 4): a result-page cursor must not share the bag with a
    new question / clarification / continuation. Ask HTTP already enforces
    question XOR result_page_cursor; this validator is the module-level guard.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    question: str | None = None
    principal: Principal
    correlation_id: str = Field(min_length=1)
    business_date: date | None = None
    max_rows: int = Field(default=20, ge=1, le=50)
    max_answer_chars: int = Field(default=8_000, ge=1, le=8_000)
    continuation: str | None = None
    clarification_reply: str | None = None
    clarification_prompt: str | None = None
    owner_hint: BusinessQueryOwnerHint | None = None
    page_cursor: str | None = None
    response_policy: Literal["strict", "allow_partial"] = "allow_partial"
    # Prior structured turns as (role, text) pairs — roles are "human" / "ai".
    history: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def _exclusive_page_cursor(self) -> BusinessQueryRequest:
        if self.page_cursor is None:
            return self
        if (
            self.question is not None
            or self.continuation is not None
            or self.clarification_reply is not None
            or self.clarification_prompt is not None
            or self.history
        ):
            raise ValueError(
                "page_cursor cannot combine with question, continuation, or clarification_reply"
            )
        return self


class NewBusinessQueryRequest(BaseModel):
    """Explicit closed request type for new questions and continuations."""

    model_config = ConfigDict(strict=True, extra="forbid")

    question: str | None = None
    principal: Principal
    correlation_id: str = Field(min_length=1)
    business_date: date | None = None
    max_rows: int = Field(default=20, ge=1, le=50)
    max_answer_chars: int = Field(default=8_000, ge=1, le=8_000)
    continuation: str | None = None
    clarification_reply: str | None = None
    clarification_prompt: str | None = None
    owner_hint: BusinessQueryOwnerHint | None = None
    response_policy: Literal["strict", "allow_partial"] = "allow_partial"
    history: tuple[tuple[str, str], ...] = ()

    def as_wire(self) -> BusinessQueryRequest:
        """Ask compose uses this; the module still consumes BusinessQueryRequest."""
        return BusinessQueryRequest(
            question=self.question,
            principal=self.principal,
            correlation_id=self.correlation_id,
            business_date=self.business_date,
            max_rows=self.max_rows,
            max_answer_chars=self.max_answer_chars,
            continuation=self.continuation,
            clarification_reply=self.clarification_reply,
            clarification_prompt=self.clarification_prompt,
            owner_hint=self.owner_hint,
            response_policy=self.response_policy,
            history=self.history,
        )


def build_new_business_query_wire(
    *,
    question: str | None,
    principal: Principal,
    correlation_id: str,
    continuation: str | None = None,
    clarification_reply: str | None = None,
    clarification_prompt: str | None = None,
    owner_hint: BusinessQueryOwnerHint | None = None,
    response_policy: Literal["strict", "allow_partial"] = "allow_partial",
    history: tuple[tuple[str, str], ...] = (),
    business_date: date | None = None,
    max_rows: int = 20,
    max_answer_chars: int = 8_000,
) -> BusinessQueryRequest:
    """Compose a closed new-question wire request for Ask."""
    return NewBusinessQueryRequest(
        question=question,
        principal=principal,
        correlation_id=correlation_id,
        business_date=business_date,
        max_rows=max_rows,
        max_answer_chars=max_answer_chars,
        continuation=continuation,
        clarification_reply=clarification_reply,
        clarification_prompt=clarification_prompt,
        owner_hint=owner_hint,
        response_policy=response_policy,
        history=history,
    ).as_wire()


class ResultPageRequest(BaseModel):
    """Explicit closed request type for cursor-based pagination."""

    model_config = ConfigDict(strict=True, extra="forbid")

    page_cursor: str = Field(min_length=1)
    principal: Principal
    correlation_id: str = Field(min_length=1)
    business_date: date | None = None
    max_rows: int = Field(default=20, ge=1, le=50)
    max_answer_chars: int = Field(default=8_000, ge=1, le=8_000)
    response_policy: Literal["strict", "allow_partial"] = "allow_partial"

    def as_wire(self) -> BusinessQueryRequest:
        """Ask compose uses this; the module still consumes BusinessQueryRequest."""
        return BusinessQueryRequest(
            question=None,
            page_cursor=self.page_cursor,
            principal=self.principal,
            correlation_id=self.correlation_id,
            business_date=self.business_date,
            max_rows=self.max_rows,
            max_answer_chars=self.max_answer_chars,
            response_policy=self.response_policy,
        )


BundleResolver = Callable[[str], DefinitionBundle]
ScopeFn = Callable[[BusinessQueryPlan, Principal, DefinitionBundle], ScopedPlan]
Presenter = Callable[[BusinessQueryPlan, Answered], str]
