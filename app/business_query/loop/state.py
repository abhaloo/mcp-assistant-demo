"""Typed loop state. Serializable IDs and results only; no Principal or sessions."""

from __future__ import annotations

from typing import Literal, TypedDict

from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.tools.contracts import (
    BusinessQueryInvocation,
    CatalogRefusal,
    DocumentInvocation,
    ToolFailure,
    ToolResult,
)
from app.tools.turn_contracts import QueryTypeName, ToolTurnExecution

StopReason = Literal["terminal", "cap", "budget"]


class LoopState(TypedDict, total=False):
    query_type: QueryTypeName
    bq_requested: bool
    document_call: DocumentInvocation | None
    bq_call: BusinessQueryInvocation | None
    bq_result: CommittedBqResult | AskBusinessQueryResult | None
    document_result: ToolResult | ToolFailure | None
    refusal: CatalogRefusal | None
    run_sql: bool
    run_document: bool
    execution: ToolTurnExecution | None
    stop: StopReason | None
