"""Request models, protocol sinks, and adapter contracts for wire orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth import Principal
from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Answered
from app.business_query.plan import BusinessQueryPlan
from app.business_query.plan.dialogue import AssistantTurn, DialogueTurn, UserTurn
from app.business_query.plan.plan_patch import PlanPatch

# A legacy history item is a (role, text) pair.
_ROLE_TEXT_PAIR = 2

# Display budget for streamed Ask answers. Default equals the hard cap.
MAX_ANSWER_CHARS = 16_000


class BusinessQueryOwnerHint(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: str
    record_id: int | str
    binding_member: str | None = None
    source: Literal["page", "binding"] = "page"


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
    max_answer_chars: int = Field(default=MAX_ANSWER_CHARS, ge=1, le=MAX_ANSWER_CHARS)
    continuation: str | None = None
    clarification_reply: str | None = None
    clarification_prompt: str | None = None
    owner_hint: BusinessQueryOwnerHint | None = None
    page_cursor: str | None = None
    response_policy: Literal["strict", "allow_partial"] = "allow_partial"
    history: tuple[DialogueTurn, ...] = ()
    reading: str | None = None
    patch: PlanPatch | None = None

    @field_validator("history", mode="before")
    @classmethod
    def _coerce_history(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            coerced: list[DialogueTurn] = []
            for item in value:
                if isinstance(item, tuple) and len(item) == _ROLE_TEXT_PAIR:
                    role, text = item
                    if role in ("human", "user"):
                        coerced.append(UserTurn(text=text))
                    elif role in ("ai", "assistant"):
                        coerced.append(AssistantTurn(text=text))
                    else:
                        raise ValueError(f"unsupported dialogue role: {role!r}")
                else:
                    coerced.append(item)
            return tuple(coerced)
        return value

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
            or self.reading is not None
            or self.patch is not None
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
    max_answer_chars: int = Field(default=MAX_ANSWER_CHARS, ge=1, le=MAX_ANSWER_CHARS)
    continuation: str | None = None
    clarification_reply: str | None = None
    clarification_prompt: str | None = None
    owner_hint: BusinessQueryOwnerHint | None = None
    response_policy: Literal["strict", "allow_partial"] = "allow_partial"
    history: tuple[DialogueTurn, ...] = ()
    reading: str | None = None
    patch: PlanPatch | None = None

    @field_validator("history", mode="before")
    @classmethod
    def _coerce_history(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            coerced: list[DialogueTurn] = []
            for item in value:
                if isinstance(item, tuple) and len(item) == _ROLE_TEXT_PAIR:
                    role, text = item
                    if role in ("human", "user"):
                        coerced.append(UserTurn(text=text))
                    elif role in ("ai", "assistant"):
                        coerced.append(AssistantTurn(text=text))
                    else:
                        raise ValueError(f"unsupported dialogue role: {role!r}")
                else:
                    coerced.append(item)
            return tuple(coerced)
        return value

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
            reading=self.reading,
            patch=self.patch,
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
    history: tuple[DialogueTurn, ...] = (),
    business_date: date | None = None,
    max_rows: int = 20,
    max_answer_chars: int = MAX_ANSWER_CHARS,
    reading: str | None = None,
    patch: PlanPatch | None = None,
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
        reading=reading,
        patch=patch,
    ).as_wire()


class ResultPageRequest(BaseModel):
    """Explicit closed request type for cursor-based pagination."""

    model_config = ConfigDict(strict=True, extra="forbid")

    page_cursor: str = Field(min_length=1)
    principal: Principal
    correlation_id: str = Field(min_length=1)
    business_date: date | None = None
    max_rows: int = Field(default=20, ge=1, le=50)
    max_answer_chars: int = Field(default=MAX_ANSWER_CHARS, ge=1, le=MAX_ANSWER_CHARS)
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
