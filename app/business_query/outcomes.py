from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.result_presentation import ResultPresentation

ResultCompleteness = Literal["complete", "partial"]
CoverageStatus = Literal["verified_complete", "incomplete", "unknown"]

_UNSUPPORTED_REASON_CODES = Literal[
    "member_not_found",
    "value_not_found",
    "no_join_path",
    "capability_disabled",
    "grain_unexpressible",
    "period_dimension_missing",
    "fanout_unsafe",
    "measure_filter_unsupported",
    "optional_join_unsupported",
    "unsupported_operator",
]


class AdapterUnsupported(Exception):
    """Internal-only: THIS adapter cannot compile what the bundle CAN express."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code


class PlanRefused(Exception):
    """Internal-only: the plan+bundle combination is unsafe or unexpressible for
    EVERY adapter. The module maps this to terminal Unsupported(reason_code).
    """

    def __init__(
        self,
        reason_code: Literal[
            _UNSUPPORTED_REASON_CODES,
            "invalid_business_timezone",
            "unsupported_relative_period",
        ],
        check_site: str | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.check_site = check_site


class ClarificationRequired(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    outcome: Literal["clarification_required"] = "clarification_required"
    question: str
    continuation: str
    prompt: str | None = None
    choices: list[Any] = Field(default_factory=list)
    allow_free_text: bool = True
    resolver_query_id: str | None = None
    disambiguation: Any = None


class Unsupported(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    outcome: Literal["unsupported"] = "unsupported"
    reason_code: _UNSUPPORTED_REASON_CODES
    message: str
    resolver_query_id: str | None = None


class Incomplete(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    outcome: Literal["incomplete"] = "incomplete"
    reason_code: Literal[
        "budget",
        "timeout",
        "adapter_invalid",
        "request_conflict",
        "no_progress",
        "cursor_expired",
        "evidence_unavailable",
        "detail_source_unavailable",
    ]
    resolver_query_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _alias_reason(cls, data: object) -> object:
        if isinstance(data, dict):
            data = dict(data)
            if "reason" in data and "reason_code" not in data:
                data["reason_code"] = data.pop("reason")
        return data


class Denied(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    outcome: Literal["denied"] = "denied"
    message: str = "business query tools are currently unavailable"
    resolver_query_id: str | None = None
    reason_code: (
        Literal[
            "cursor_invalid",
            "cursor_scope_mismatch",
            "policy_denied",
        ]
        | None
    ) = None


from app.business_query.plan.query_plan import BusinessQueryPlan  # noqa: E402


class RecordDetail(BaseModel):
    """Authorized typed detail record (ADR 0030)."""

    model_config = ConfigDict(strict=True, extra="forbid")
    family: str
    typed_value: Any = None
    display_value: str | None = None
    revision_hash: str | None = None
    profile_revision_hash: str | None = None
    definition_revision: str | None = None
    profile_revision: str | None = None
    observation_version: int | None = None
    validation: str | None = None
    coverage_status: str | None = None
    coverage: CoverageStatus | None = None
    provenance: Any = None
    owner_resource: str | None = None
    owner_id: int | str | None = None


class NextPageAction(BaseModel):
    """Deterministic pagination action for partial results."""

    model_config = ConfigDict(strict=True, extra="forbid")
    action: Literal["next_page"] = "next_page"
    cursor: str
    page_size: int = 20
    expires_at: datetime | None = None


class TimingReceipt(BaseModel):
    """Redacted stage execution timing receipt."""

    model_config = ConfigDict(strict=True, extra="forbid")
    classifier_ms: float | None = None
    conversation_ms: float | None = None
    planner_ms: float | None = None
    sql_ms: float | None = None
    detail_read_ms: float | None = None
    rich_render_ms: float | None = None
    finalization_ms: float | None = None
    first_progress_ms: float | None = None
    total_ms: float | None = None
    route: str | None = None
    provider: str | None = None
    deployment: str | None = None
    answer_query_id: str | None = None
    terminal_reason: str | None = None

    def reconcile_total(self) -> float:
        """Calculate the sum of all recorded stage durations."""
        stages = [
            self.classifier_ms,
            self.conversation_ms,
            self.planner_ms,
            self.sql_ms,
            self.detail_read_ms,
            self.rich_render_ms,
            self.finalization_ms,
        ]
        return sum(s for s in stages if s is not None)


class BusinessQueryReceipt(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    answer_query_id: str
    root_answer_query_id: str | None = None
    bundle_hash: str
    manifest_hash: str
    plan_fingerprint: str
    row_count: int
    executed_at: datetime
    resolver_query_id: str | None = None
    policy_hash: str | None = None
    definition_hash: str | None = None
    profile_hash: str | None = None
    record_referent_digest: str | None = None
    # Internal continuation provenance. It is available to the Query Record
    # projection but excluded from the public JSON/SSE receipt.
    source_question: str | None = Field(default=None, exclude=True)


class RecordPreview(BaseModel):
    """Enriched summary metadata for hover tooltips on record chips.

    Canonically defined here (ADR 0024) to avoid upward dependency from domain
    outcomes to top-level app.models.schemas. Re-exported in app.models.schemas.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: Literal["job", "invoice", "customer", "customer_order", "inventory"]
    title: str = Field(min_length=1, max_length=128)
    subtitle: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, max_length=64)
    badge_variant: Literal["success", "warning", "danger", "info", "neutral"] = "neutral"
    amount: str | None = Field(default=None, max_length=64)
    updated_at: str | None = Field(default=None, max_length=64)


class RecordRef(BaseModel):
    """Sealed bindable-id sidecar (ADR 0053 Decision 4).

    Bindable ids never live in ``Answered.rows`` — they travel here instead,
    so ``declared_result_members`` never sees an undeclared id column.
    """

    model_config = ConfigDict(strict=True, extra="forbid")
    resource: str
    record_id: int
    label: str | None = None
    preview: RecordPreview | None = None
    row_index: int | None = Field(default=None, ge=0, exclude=True)


class RecordResult(BaseModel):
    """One record with its fields and authorized detail facts attached."""

    model_config = ConfigDict(strict=True, extra="forbid")
    ref: RecordRef
    fields: dict[str, Any] = Field(default_factory=dict)
    details: tuple[RecordDetail, ...] = ()


class AggregateResult(BaseModel):
    """A scalar or grouped row that does not identify one bindable record."""

    model_config = ConfigDict(strict=True, extra="forbid")
    fields: dict[str, Any] = Field(default_factory=dict)


ResultValueKind = Literal[
    "boolean", "integer", "decimal", "percent", "currency", "string", "date", "datetime"
]
DECIMAL_VALUE_KINDS = frozenset({"decimal", "percent", "currency"})
ResultColumnRole = Literal["current", "previous", "delta", "delta_pct"]


_COMPARISON_SUFFIX_BY_ROLE: dict[ResultColumnRole, str] = {
    "current": "",
    "previous": "__previous",
    "delta": "__delta",
    "delta_pct": "__delta_pct",
}


def comparison_member(measure: str, role: ResultColumnRole) -> str:
    """The result key of one comparison column; the wire naming lives here only."""
    return f"{measure}{_COMPARISON_SUFFIX_BY_ROLE[role]}"


def comparison_keys(measure: str) -> dict[str, ResultColumnRole]:
    """Map comparison column names to their presentation roles."""
    return {comparison_member(measure, role): role for role in _COMPARISON_SUFFIX_BY_ROLE}


class ResultColumn(BaseModel):
    """Wire-facing type of one result column, declared by the Billing bundle."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    key: str = Field(min_length=1, max_length=128)
    value_kind: ResultValueKind
    currency_key: str | None = None
    is_identifier: bool = False
    is_count: bool = False
    role: ResultColumnRole | None = None


class Answered(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    outcome: Literal["answered"] = "answered"
    answer_text: str
    rows: list[dict]
    total_row_count: int
    receipt: BusinessQueryReceipt
    record_refs: tuple[RecordRef, ...] = ()
    plan: BusinessQueryPlan | None = None
    next_cursor: str | None = None
    record_details: list[RecordDetail] = Field(default_factory=list)
    result_completeness: ResultCompleteness = "complete"
    coverage_status: CoverageStatus = "verified_complete"
    next_page_action: NextPageAction | None = None
    timing: TimingReceipt | None = None
    failed_detail_families: tuple[str, ...] = ()
    companion_answered: tuple[Answered, ...] = ()
    columns: tuple[ResultColumn, ...] = ()
    presentation: ResultPresentation | None = None
    # Scoped plan fingerprint (plan + forced predicates + response policy) so a
    # retained answer can prove exact scope equality later; never on the wire.
    scope_fingerprint: str | None = None


class UnifiedResultEnvelope(BaseModel):
    """Shared result envelope for JSON and SSE."""

    model_config = ConfigDict(strict=True, extra="forbid")
    schema_version: Literal[1] = 1
    evidence_sealed: bool = False
    answer_query_id: str
    root_answer_query_id: str | None = None
    answer_text: str
    rows: list[dict] = Field(default_factory=list)
    records: tuple[RecordResult, ...] = ()
    aggregates: tuple[AggregateResult, ...] = ()
    record_refs: tuple[RecordRef, ...] = ()
    receipt: BusinessQueryReceipt
    returned_row_count: int
    total_row_count: int
    result_completeness: ResultCompleteness
    coverage_status: CoverageStatus
    record_details: list[RecordDetail] = Field(default_factory=list)
    provenance: str | None = None
    next_page_action: NextPageAction | None = None
    bundle_hash: str
    policy_hash: str | None = None
    manifest_hash: str | None = None
    definition_hash: str | None = None
    profile_hash: str | None = None
    unresolved_effect: str | None = None
    timing: TimingReceipt | None = None
    failed_detail_families: tuple[str, ...] = ()
    columns: tuple[ResultColumn, ...] = ()
    presentation: ResultPresentation | None = None


BusinessQueryWireDisposition = Literal[
    "answered",
    "clarification_required",
    "unsupported",
    "incomplete",
    "denied",
]


class BusinessQueryWireOutcome(BaseModel):
    """Canonical transport value shared by JSON and SSE Ask adapters.

    ``envelope`` is populated only for an answered query.  Refusal and
    clarification values stay typed at the same boundary so transports do not
    silently drop continuation tokens, resolver ids, or terminal reason codes.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    outcome: BusinessQueryWireDisposition
    envelope: UnifiedResultEnvelope | None = None
    envelopes: list[UnifiedResultEnvelope] = Field(default_factory=list)
    question: str | None = None
    continuation: str | None = None
    choices: list[Any] = Field(default_factory=list)
    resolver_query_id: str | None = None
    reason_code: str | None = None
    message: str | None = None
    disambiguation: Any = None

    @model_validator(mode="after")
    def _validate_envelope_for_outcome(self) -> BusinessQueryWireOutcome:
        if self.outcome == "answered" and self.envelope is None:
            raise ValueError("answered Business Query wire outcome requires a sealed envelope")
        if self.outcome != "answered" and self.envelope is not None:
            raise ValueError("only answered Business Query wire outcomes may carry an envelope")
        if self.envelope is not None and self.envelope.evidence_sealed is not True:
            raise ValueError("Business Query envelope evidence must be sealed")
        if self.outcome != "answered" and self.envelopes:
            raise ValueError("only answered Business Query wire outcomes may carry envelopes")
        if self.outcome == "answered" and self.envelope is not None:
            if not self.envelopes:
                self.envelopes = [self.envelope]
            elif self.envelopes[0] != self.envelope:
                raise ValueError("singular envelope must equal envelopes[0]")
        return self


def serialize_unified_envelope(
    answered: Answered,
    *,
    timing: TimingReceipt | None = None,
    provenance: str | None = None,
    unresolved_effect: str | None = None,
) -> dict[str, Any]:
    """Serialize shared result envelope for JSON endpoints and SSE terminal completion frames."""
    return _build_unified_envelope(
        answered,
        timing=timing,
        provenance=provenance,
        unresolved_effect=unresolved_effect,
    ).model_dump(mode="json")


def _build_unified_envelope(
    answered: Answered,
    *,
    timing: TimingReceipt | None = None,
    provenance: str | None = None,
    unresolved_effect: str | None = None,
    evidence_sealed: bool = False,
) -> UnifiedResultEnvelope:
    """Build the typed envelope before a transport chooses its wire encoding."""
    eff_timing = timing or answered.timing
    eff_provenance = provenance or answered.receipt.manifest_hash
    policy_hash = answered.receipt.policy_hash or answered.receipt.manifest_hash

    completeness: ResultCompleteness = answered.result_completeness
    if answered.total_row_count > len(answered.rows) and completeness == "complete":
        completeness = "partial"

    records, aggregates = _partition_record_results(answered)

    return UnifiedResultEnvelope(
        schema_version=1,
        evidence_sealed=evidence_sealed,
        answer_query_id=answered.receipt.answer_query_id,
        root_answer_query_id=answered.receipt.root_answer_query_id,
        answer_text=answered.answer_text,
        rows=list(answered.rows),
        records=records,
        aggregates=aggregates,
        record_refs=answered.record_refs,
        receipt=answered.receipt,
        returned_row_count=len(answered.rows),
        total_row_count=answered.total_row_count,
        result_completeness=completeness,
        coverage_status=answered.coverage_status,
        record_details=list(answered.record_details),
        provenance=eff_provenance,
        next_page_action=answered.next_page_action,
        bundle_hash=answered.receipt.bundle_hash,
        policy_hash=policy_hash,
        manifest_hash=answered.receipt.manifest_hash,
        definition_hash=answered.receipt.definition_hash,
        profile_hash=answered.receipt.profile_hash,
        unresolved_effect=unresolved_effect,
        timing=eff_timing,
        failed_detail_families=answered.failed_detail_families,
        columns=answered.columns,
        presentation=answered.presentation,
    )


def _partition_record_results(
    answered: Answered,
) -> tuple[tuple[RecordResult, ...], tuple[AggregateResult, ...]]:
    """Attach details to rows only when an executor-minted record identity proves ownership."""
    rows = list(answered.rows)
    refs_by_row: dict[int, list[RecordRef]] = {}
    unbound_refs: list[RecordRef] = []
    for ref in answered.record_refs:
        if ref.row_index is not None and ref.row_index < len(rows):
            refs_by_row.setdefault(ref.row_index, []).append(ref)
        else:
            unbound_refs.append(ref)

    # N-1 adapters minted one ref per row but did not record the binding index.
    if not refs_by_row and len(unbound_refs) == len(rows):
        for index, ref in enumerate(unbound_refs):
            refs_by_row[index] = [ref]
        unbound_refs = []

    # A singular record-detail answer can recover its typed owner identity from
    # the signed detail projection without guessing a resource in this layer.
    if len(rows) == 1 and not refs_by_row:
        owners = {
            (detail.owner_resource, detail.owner_id)
            for detail in answered.record_details
            if detail.owner_resource is not None and detail.owner_id is not None
        }
        if len(owners) == 1:
            resource, owner_id = next(iter(owners))
            try:
                numeric_owner_id = int(owner_id)
            except (TypeError, ValueError):
                numeric_owner_id = None
            if numeric_owner_id is not None:
                refs_by_row[0] = [
                    RecordRef(resource=resource, record_id=numeric_owner_id, row_index=0)
                ]

    records: list[RecordResult] = []
    aggregates: list[AggregateResult] = []
    for index, row in enumerate(rows):
        refs = refs_by_row.get(index, [])
        if not refs:
            aggregates.append(AggregateResult(fields=dict(row)))
            continue
        for ref in refs:
            details = tuple(
                detail
                for detail in answered.record_details
                if detail.owner_id is not None
                and str(detail.owner_id) == str(ref.record_id)
                and (detail.owner_resource is None or detail.owner_resource == ref.resource)
            )
            records.append(RecordResult(ref=ref, fields=dict(row), details=details))

    records.extend(RecordResult(ref=ref) for ref in unbound_refs)
    return tuple(records), tuple(aggregates)


BusinessQueryOutcome = Annotated[
    Answered | ClarificationRequired | Unsupported | Incomplete | Denied,
    Field(discriminator="outcome"),
]


def serialize_business_query_outcome(
    outcome: Answered | ClarificationRequired | Unsupported | Incomplete | Denied,
    *,
    timing: TimingReceipt | None = None,
    provenance: str | None = None,
    unresolved_effect: str | None = None,
    evidence_sealed: bool = False,
) -> BusinessQueryWireOutcome:
    """Map one domain outcome to the canonical JSON/SSE wire value."""

    if isinstance(outcome, Answered):
        if not evidence_sealed:
            return BusinessQueryWireOutcome(
                outcome="incomplete",
                reason_code="evidence_unavailable",
            )
        envelope = _build_unified_envelope(
            outcome,
            timing=timing,
            provenance=provenance,
            unresolved_effect=unresolved_effect,
            evidence_sealed=True,
        )
        companion_envelopes = [
            _build_unified_envelope(
                companion,
                timing=timing,
                provenance=provenance,
                unresolved_effect=unresolved_effect,
                evidence_sealed=True,
            )
            for companion in outcome.companion_answered
        ]
        return BusinessQueryWireOutcome(
            outcome="answered",
            envelope=envelope,
            envelopes=[envelope, *companion_envelopes],
        )
    if isinstance(outcome, ClarificationRequired):
        return BusinessQueryWireOutcome(
            outcome=outcome.outcome,
            question=outcome.question,
            continuation=outcome.continuation,
            choices=list(outcome.choices),
            resolver_query_id=outcome.resolver_query_id,
            disambiguation=outcome.disambiguation,
        )
    if isinstance(outcome, Unsupported):
        return BusinessQueryWireOutcome(
            outcome=outcome.outcome,
            reason_code=outcome.reason_code,
            message=outcome.message,
            resolver_query_id=outcome.resolver_query_id,
        )
    if isinstance(outcome, Incomplete):
        return BusinessQueryWireOutcome(
            outcome=outcome.outcome,
            reason_code=outcome.reason_code,
            resolver_query_id=outcome.resolver_query_id,
        )
    if isinstance(outcome, Denied):
        return BusinessQueryWireOutcome(
            outcome=outcome.outcome,
            reason_code=outcome.reason_code,
            message=outcome.message,
            resolver_query_id=outcome.resolver_query_id,
        )
    raise TypeError(f"unexpected BusinessQueryOutcome: {type(outcome)!r}")


def resolver_record_fields(outcome: object | None) -> tuple[str | None, str | None]:
    """Lookup id and linked disposition for Query Record / Ask / eval consumers."""
    if outcome is None:
        return None, None
    if isinstance(outcome, Answered):
        lookup_id = outcome.receipt.resolver_query_id
        return lookup_id, ("exact" if lookup_id else None)
    if isinstance(outcome, ClarificationRequired):
        lookup_id = outcome.resolver_query_id
        return (lookup_id, "ambiguous") if lookup_id else (None, None)
    if isinstance(outcome, (Unsupported, Incomplete)):
        lookup_id = outcome.resolver_query_id
        return (lookup_id, "none") if lookup_id else (None, None)
    if isinstance(outcome, Denied):
        return outcome.resolver_query_id, None
    return None, None


def attach_resolver(
    outcome: BusinessQueryOutcome, resolver_query_id: str | None
) -> BusinessQueryOutcome:
    """Stamp resolver lookup identity onto every query() return. Idempotent if already set."""
    if not resolver_query_id:
        return outcome
    if isinstance(outcome, Answered):
        if outcome.receipt.resolver_query_id:
            return outcome
        return outcome.model_copy(
            update={
                "receipt": outcome.receipt.model_copy(
                    update={"resolver_query_id": resolver_query_id}
                )
            }
        )
    if isinstance(outcome, (ClarificationRequired, Unsupported, Incomplete, Denied)):
        if outcome.resolver_query_id:
            return outcome
        return outcome.model_copy(update={"resolver_query_id": resolver_query_id})
    return outcome
