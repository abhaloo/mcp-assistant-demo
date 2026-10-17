"""Typed tool requests, admission vocabulary, and outcome types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import Principal
from app.business_query.plan.planned_set import PlannedQuerySet
from app.business_query.plan.query_plan import BusinessQueryPlan
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.core.turn_budget import TurnBudget
from app.models.record_context import RecordContext
from app.rag.retrieval.document_contracts import DocumentFailure, DocumentSearchResult

_MAX_DOCUMENT_QUERY_CHARS = 4096

BusinessQueryHandler = Callable[
    ["BusinessQueryInvocation", "ToolContext"],
    Awaitable[CommittedBqResult | AskBusinessQueryResult],
]
DocumentHandler = Callable[
    ["DocumentSearchInput", "ToolContext"],
    Awaitable[DocumentSearchResult | DocumentFailure],
]


class DocumentSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=_MAX_DOCUMENT_QUERY_CHARS)

    @field_validator("query")
    @classmethod
    def _strip_and_bound_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or len(stripped) > _MAX_DOCUMENT_QUERY_CHARS:
            raise ValueError(
                f"query must be 1..{_MAX_DOCUMENT_QUERY_CHARS} characters after whitespace trim"
            )
        return stripped


class BusinessQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    primary: BusinessQueryPlan
    companions: tuple[BusinessQueryPlan, ...] = ()

    @field_validator("companions")
    @classmethod
    def _at_most_one_companion(
        cls, value: tuple[BusinessQueryPlan, ...]
    ) -> tuple[BusinessQueryPlan, ...]:
        if len(value) > 1:
            raise ValueError("at most one companion plan")
        return value

    def to_planned_query_set(self) -> PlannedQuerySet:
        return PlannedQuerySet(primary=self.primary, companions=self.companions)


class DocumentInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["document_search"]
    version: Literal[1]
    invocation_id: str = Field(min_length=1, max_length=64)
    arguments: DocumentSearchInput


class BusinessQueryInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["business_query"]
    version: Literal[1]
    invocation_id: str = Field(min_length=1, max_length=64)
    arguments: BusinessQueryInput


ToolInvocation = Annotated[
    BusinessQueryInvocation | DocumentInvocation,
    Field(discriminator="name"),
]


@dataclass(frozen=True)
class ToolContext:
    principal: Principal
    correlation_id: str
    budget: TurnBudget
    record_context: RecordContext | None
    origin: Literal["ask", "eval"]

    def __repr__(self) -> str:
        return (
            f"ToolContext(correlation_id={self.correlation_id!r}, "
            f"origin={self.origin!r}, record_context={'set' if self.record_context else None})"
        )


@dataclass(frozen=True)
class CatalogRefusal:
    code: Literal[
        "unknown_tool",
        "unsupported_version",
        "invalid_arguments",
        "invalid_selection",
        "write_forbidden",
    ]
    invocation_ids: tuple[str, ...]


@dataclass(frozen=True)
class ToolFailure:
    invocation_id: str
    status: Literal["denied", "unavailable", "timeout", "failed"]
    code: Literal[
        "policy_denied",
        "circuit_open",
        "capacity_exhausted",
        "retriever_unavailable",
        "stage_timeout",
        "invalid_provenance",
        "output_limit",
    ]


@dataclass(frozen=True)
class ToolResult:
    invocation_id: str
    value: object

    def __repr__(self) -> str:
        return f"ToolResult(invocation_id={self.invocation_id!r}, value=<redacted>)"


ToolOutcome = ToolResult | ToolFailure


@dataclass(frozen=True)
class AdmittedSelection:
    invocations: tuple[BusinessQueryInvocation | DocumentInvocation, ...]
