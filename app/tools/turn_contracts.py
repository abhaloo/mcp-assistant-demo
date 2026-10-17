"""Neutral turn request, context, and execution types. No LangGraph types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from app.auth import Principal
from app.business_query.ports import BusinessProgressSink
from app.core.turn_budget import TurnBudget
from app.models.record_context import RecordContext

if TYPE_CHECKING:
    from app.business_query.plan import PlannedQuerySet
    from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
    from app.tools.catalog import ToolCatalog
    from app.tools.contracts import (
        BusinessQueryInvocation,
        CatalogRefusal,
        DocumentInvocation,
        ToolFailure,
        ToolResult,
    )
else:
    DocumentInvocation = Any
    BusinessQueryInvocation = Any
    ToolResult = Any
    ToolFailure = Any
    CatalogRefusal = Any
    ToolCatalog = Any

QueryTypeName = Literal["semantic", "structured", "both"]


@runtime_checkable
class BqTurnPort(Protocol):
    async def prepare(self) -> PlannedQuerySet | AskBusinessQueryResult: ...

    async def execute(
        self, call: BusinessQueryInvocation
    ) -> CommittedBqResult | AskBusinessQueryResult: ...

    async def aclose(self) -> None: ...


class ToolTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_type: QueryTypeName
    bq_requested: bool
    document: DocumentInvocation | None = None


@dataclass(frozen=True)
class ToolTurnContext:
    principal: Principal
    correlation_id: str
    budget: TurnBudget
    catalog: ToolCatalog
    bq: BqTurnPort | None
    progress: BusinessProgressSink | None
    record_context: RecordContext | None
    origin: Literal["ask", "eval"]

    def __repr__(self) -> str:
        return (
            f"ToolTurnContext(correlation_id={self.correlation_id!r}, "
            f"origin={self.origin!r}, bq={'set' if self.bq else None})"
        )


@dataclass(frozen=True)
class ToolTurnExecution:
    bq: CommittedBqResult | AskBusinessQueryResult | None
    document: ToolResult | ToolFailure | None
    refusal: CatalogRefusal | None
