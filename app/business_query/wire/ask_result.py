"""Ask BQ DTO and committed-result value (ADR 0076)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.business_query.compile.pagination.plan_payload import DerivedPlanPayload
from app.business_query.outcomes import BusinessQueryWireOutcome
from app.business_query.plan import BusinessQueryPlan
from app.business_query.wire.failure_note import FailureNote
from app.business_query.wire.request import BusinessQueryOwnerHint
from app.models.schemas import RecordLink
from app.models.tool_results import TurnResult


@dataclass(frozen=True)
class RetainedBqMember:
    """One typed BQ member preserving plan and fingerprints before wire serialization."""

    ordinal: int  # 1 = primary, 2 = companion; matches BusinessQueryWireOutcome.envelopes order
    answer_query_id: str  # UnifiedResultEnvelope.answer_query_id
    plan: BusinessQueryPlan
    plan_fingerprint: str
    scope_fingerprint: str
    derived_payload: DerivedPlanPayload | None = None
    owner_hint: BusinessQueryOwnerHint | None = None


@dataclass(frozen=True)
class AskBusinessQueryResult:
    disposition: Literal[
        "answered",
        "clarification_required",
        "unsupported",
        "incomplete",
        "denied",
        "shadowed",
    ]
    answer_text: str
    completion_status: Literal["complete", "incomplete"] | None
    sql_stop_reason: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    raise_capability_unavailable: bool
    reasoning_tokens: int | None = None
    provider: str | None = None
    # Sealed Record Ref sidecar, minted only from Answered.record_refs (ADR
    # 0053 Decision 5) — never rows, never SQL. Empty for every non-Answered
    # disposition and for a scalar Answered with no bindable ids.
    record_links: list[RecordLink] = field(default_factory=list)
    plan: BusinessQueryPlan | None = None
    scope_fingerprint: str | None = None
    # BQ trace block for the turn record — sql/sql_ms/planner_ms/
    # planner_repair_count/rows_returned/failure_layer, JSON-encoded. The
    # "sql" key carries the full statement with values — this lives in the
    # owned Postgres query_records table, never in logs/eval JSONL.
    bq_trace_json: str | None = None
    disambiguation: Any = None
    # Canonical domain outcome/envelope carried to both Ask transports.  The
    # older scalar fields above remain for Query Record and telemetry consumers.
    business_query: BusinessQueryWireOutcome | None = None
    # Retained BQ members for evidence retention; declared so a
    # dataclasses.replace rebuild keeps them. Empty for every non-Answered
    # disposition and for a shadow result.
    retained_members: tuple[RetainedBqMember, ...] = ()
    # The whole-turn result (spec §6); present only when the tool layer is enabled.
    turn_result: TurnResult | None = None
    failure_note: FailureNote | None = None


@dataclass(frozen=True)
class CommittedBqResult:
    result: AskBusinessQueryResult
    answer_queries: tuple[str, ...]
