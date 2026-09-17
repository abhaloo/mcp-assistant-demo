"""Pure reduction of one tool turn into a TurnResult (spec §6 truth table)."""

from __future__ import annotations

from typing import Literal

from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.models.tool_results import (
    Completeness,
    ComponentEvidence,
    ComponentStatus,
    OmissionReason,
    ToolName,
    ToolOmission,
    TurnOutcomeType,
    TurnResult,
)
from app.rag.retrieval.document_contracts import DocumentSearchResult
from app.tools.contracts import ToolFailure, ToolResult
from app.tools.turn_contracts import ToolTurnExecution, ToolTurnRequest

BqState = Literal[
    "committed",
    "denied",
    "clarification_required",
    "unsupported",
    "shadowed",
    "timeout",
    "incomplete",
]
DocState = Literal["useful", "empty", "failed"]

BqBranch = CommittedBqResult | AskBusinessQueryResult | None
DocBranch = ToolResult | ToolFailure | None

_BQ_OMISSION: dict[BqState, OmissionReason] = {
    "denied": "policy_denied",
    "clarification_required": "clarification_required",
    "unsupported": "unsupported",
    "shadowed": "shadowed",
    "timeout": "stage_timeout",
    "incomplete": "incomplete",
}
_BQ_STATUS: dict[BqState, ComponentStatus] = {
    "committed": "succeeded",
    "denied": "denied",
    "clarification_required": "clarification_required",
    "unsupported": "unsupported",
    "shadowed": "shadowed",
    "timeout": "timeout",
    "incomplete": "incomplete",
}


def _bq_state(bq: BqBranch) -> BqState:
    if isinstance(bq, CommittedBqResult):
        return "committed"
    if not isinstance(bq, AskBusinessQueryResult):
        return "incomplete"
    if bq.disposition in ("denied", "clarification_required", "unsupported", "shadowed"):
        return bq.disposition
    timed_out = bq.sql_stop_reason == "timeout" or (
        bq.business_query is not None and bq.business_query.reason_code == "timeout"
    )
    return "timeout" if timed_out else "incomplete"


def _doc_state(doc: DocBranch) -> DocState:
    if not isinstance(doc, ToolResult):
        return "failed"
    if isinstance(doc.value, DocumentSearchResult) and doc.value.passages:
        return "useful"
    return "empty"


def _doc_omission(doc: DocBranch) -> ToolOmission:
    reason: OmissionReason = doc.code if isinstance(doc, ToolFailure) else "stage_timeout"
    return ToolOmission(tool="document_search", reason=reason)


def _bq_component(bq: BqBranch, state: BqState) -> ComponentEvidence:
    """The AQIDs are the BQ evidence identity; the sealed receipt already carries the rest."""
    answer_queries = bq.answer_queries if isinstance(bq, CommittedBqResult) else ()
    return ComponentEvidence(
        tool="business_query",
        invocation_id="bq-1",
        status=_BQ_STATUS[state],
        answer_query_ids=answer_queries,
    )


def _doc_component(request: ToolTurnRequest, doc: DocBranch) -> ComponentEvidence:
    status: ComponentStatus = "failed"
    invocation_id = request.document.invocation_id if request.document is not None else "doc-1"
    if isinstance(doc, ToolResult):
        status, invocation_id = "succeeded", doc.invocation_id
    elif isinstance(doc, ToolFailure):
        status, invocation_id = doc.status, doc.invocation_id
    return ComponentEvidence(tool="document_search", invocation_id=invocation_id, status=status)


def _document_only(
    docs: DocState | None, doc: DocBranch
) -> tuple[TurnOutcomeType, Completeness, list[ToolOmission]]:
    if docs is None:
        return "incomplete", "none", []
    if docs != "failed":
        return "answered", "full", []
    denied = isinstance(doc, ToolFailure) and doc.status == "denied"
    return ("denied" if denied else "incomplete"), "none", [_doc_omission(doc)]


def _resolve(
    bq: BqState | None,
    docs: DocState | None,
    execution: ToolTurnExecution,
) -> tuple[TurnOutcomeType, Completeness, list[ToolOmission]]:
    """The literal whole-turn row for one (bq, docs) pair."""
    doc_failed = [_doc_omission(execution.document)] if docs == "failed" else []
    if bq is None:
        return _document_only(docs, execution.document)
    if bq == "committed":
        return ("answered", "partial", doc_failed) if docs == "failed" else ("answered", "full", [])
    bq_omission = ToolOmission(tool="business_query", reason=_BQ_OMISSION[bq])
    if docs == "useful":
        outcome: TurnOutcomeType = (
            "clarification_required" if bq == "clarification_required" else "answered"
        )
        return outcome, "partial", [bq_omission]
    if bq in ("denied", "clarification_required", "unsupported", "shadowed"):
        return bq, "none", [bq_omission, *doc_failed]
    return "incomplete", "none", [bq_omission, *doc_failed]


def reduce_tool_turn(request: ToolTurnRequest, execution: ToolTurnExecution) -> TurnResult:
    """Pure domain reduction of both tool branches into one TurnResult."""
    selected: tuple[ToolName, ...] = tuple(
        name
        for name, chosen in (
            ("business_query", request.bq_requested),
            ("document_search", request.document is not None),
        )
        if chosen
    )
    if execution.refusal is not None:
        unsupported = execution.refusal.code in ("unknown_tool", "unsupported_version")
        reason: OmissionReason = "unsupported" if unsupported else "incomplete"
        return TurnResult(
            outcome_type=reason,
            completeness="none",
            trusted=False,
            selected=selected,
            omissions=tuple(ToolOmission(tool=t, reason=reason) for t in selected),
            components=(),
        )

    bq_state = _bq_state(execution.bq) if request.bq_requested else None
    doc_state = _doc_state(execution.document) if request.document is not None else None
    components: list[ComponentEvidence] = []
    if bq_state is not None:
        components.append(_bq_component(execution.bq, bq_state))
    if doc_state is not None:
        components.append(_doc_component(request, execution.document))

    outcome, completeness, omissions = _resolve(bq_state, doc_state, execution)
    return TurnResult(
        outcome_type=outcome,
        completeness=completeness,
        trusted=completeness == "full",
        selected=selected,
        omissions=tuple(omissions),
        components=tuple(components),
    )
