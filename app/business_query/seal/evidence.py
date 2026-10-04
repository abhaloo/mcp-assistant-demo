"""Evidence sealing and normalization for Business Query results (ADR 0053)."""

from __future__ import annotations

import logging
from asyncio import wait_for
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from app.auth import Principal
from app.business_query.authorize.scoping import ScopedPlan, scoped_plan_fingerprint
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import (
    Answered,
    BusinessQueryReceipt,
    Denied,
    Incomplete,
    NextPageAction,
    RecordDetail,
    RecordRef,
    ResultColumn,
    RowIdentity,
    Unsupported,
)
from app.business_query.plan import plan_fingerprint
from app.business_query.plan.value_resolver import (
    RESOLVER_VERSION,
    ResolvableType,
)
from app.business_query.seal.events import (
    BusinessQueryExecutionEvent,
    EventPayloadMode,
    ResolverQueryStart,
    ResultMember,
    answer_query_id_for,
    build_detail_evidence,
)
from app.business_query.seal.result_members import (
    declared_result_members as declared_result_members,
)
from app.business_query.seal.result_members import (
    dimension_value_kind as dimension_value_kind,
)
from app.business_query.seal.result_members import (
    measure_value_kind as measure_value_kind,
)
from app.business_query.seal.result_members import (
    normalize_event_rows as normalize_event_rows,
)
from app.business_query.seal.result_members import (
    result_columns as result_columns,
)
from app.business_query.wire.request import BusinessQueryOwnerHint
from app.rag.provenance.record_links import with_record_hrefs

if TYPE_CHECKING:
    from app.business_query.seal.events import ExecutionEventStore


@dataclass(frozen=True)
class AdapterExecutionEvidence:
    """Value-safe facts captured by the adapter that executed the real query."""

    adapter: str
    backend: str
    compiled_query_digest: str
    parameter_scope_digest: str
    result_members: tuple[ResultMember, ...]
    result_rows: tuple[dict[str, Any], ...]
    total_row_count: int
    truncated: bool
    database_identity: str
    started_at: datetime
    finished_at: datetime
    result_columns: tuple[ResultColumn, ...] = ()
    # Closed by default: an adapter that says nothing about paging gets no cursor.
    pageable: bool = False


@dataclass(frozen=True)
class UnsealedAdapterAnswer:
    """Successful executor result before durable event append and receipt minting."""

    answer_text: str
    rows: list[dict[str, Any]]
    total_row_count: int
    evidence: AdapterExecutionEvidence
    record_refs: tuple[RecordRef, ...] = ()
    record_details: tuple[RecordDetail, ...] | list[RecordDetail] = ()
    failed_detail_families: tuple[str, ...] = ()
    row_identity: RowIdentity | None = None
    selections: tuple[UnsealedSelection, ...] = ()


@dataclass(frozen=True)
class UnsealedSelection:
    """A time-group selection read in the same transaction as its answer.

    ``scoped`` is the set's own scoped plan with its own answer id, so the selection
    seals as a separate evidence event shown beside the rows.
    """

    scoped: ScopedPlan
    answer: UnsealedAdapterAnswer


def answered_from_unsealed(
    answer: UnsealedAdapterAnswer,
    scoped: ScopedPlan,
    *,
    principal: Principal,
    bundle: DefinitionBundle,
    owner_hint: BusinessQueryOwnerHint | None = None,
) -> Answered:
    """Mint a compatibility receipt after the adapter returns unsealed facts.

    Every ref leaves this seal with its href under the viewer's link grant,
    whichever adapter produced it."""
    from app.business_query.compile.pagination.plan_payload import derived_plan_payload

    derived_payload = (
        derived_plan_payload(scoped, original_question=None) if scoped.plan.derived_sets else None
    )
    return Answered(
        answer_text=answer.answer_text,
        rows=answer.rows,
        total_row_count=answer.total_row_count,
        record_refs=with_record_hrefs(answer.record_refs, answer.rows, bundle, principal),
        record_details=list(getattr(answer, "record_details", [])),
        failed_detail_families=answer.failed_detail_families,
        result_completeness=("partial" if answer.failed_detail_families else "complete"),
        coverage_status=("incomplete" if answer.failed_detail_families else "verified_complete"),
        columns=answer.evidence.result_columns,
        plan=scoped.plan,
        scope_fingerprint=scoped_plan_fingerprint(scoped),
        derived_payload=derived_payload,
        owner_hint=owner_hint,
        row_identity=answer.row_identity,
        receipt=BusinessQueryReceipt(
            answer_query_id=uuid4().hex,
            bundle_hash=bundle.content_hash,
            manifest_hash=principal.manifest_hash or "",
            plan_fingerprint=scoped_plan_fingerprint(scoped),
            row_count=len(answer.rows),
            executed_at=datetime.now(tz=UTC),
        ),
        companion_answered=tuple(
            answered_from_unsealed(
                selection.answer, selection.scoped, principal=principal, bundle=bundle
            )
            for selection in answer.selections
        ),
    )


def seal_adapter_result(
    result: UnsealedAdapterAnswer | Incomplete | Denied | Unsupported,
    scoped: ScopedPlan,
    *,
    principal: Principal,
    bundle: DefinitionBundle,
) -> Answered | Incomplete | Denied | Unsupported:
    """Single compatibility mint for adapter.execute() callers."""
    if isinstance(result, UnsealedAdapterAnswer):
        return answered_from_unsealed(result, scoped, principal=principal, bundle=bundle)
    return result


@dataclass(frozen=True)
class BusinessQueryEvidencePorts:
    """Durable evidence capability gate for Business Query execution."""

    event_store: ExecutionEventStore


@dataclass(frozen=True)
class BusinessQueryEvidenceContext:
    """Trusted per-execution identity and retention facts supplied by the runner."""

    idempotency_key: str
    project_id: str
    retention_at: datetime
    payload_mode: EventPayloadMode
    payload_classification: Literal["evaluation", "answer_release"] = "evaluation"
    route: str | None = None
    provider: str | None = None
    deployment: str | None = None
    output_mode: str | None = None
    effort: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    case_id: str | None = None
    repeat_index: int | None = None
    planner_attempt_id: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            raise ValueError("business query evidence idempotency key is required")
        if not self.project_id:
            raise ValueError("business query evidence project is required")
        if self.payload_mode is not EventPayloadMode.ENCRYPTED:
            raise ValueError("resolvable business query evidence must be encrypted")


def bare_digest(value: str) -> str:
    digest = value.removeprefix("sha256:")
    if len(digest) != 64:
        raise ValueError("business query evidence digest is invalid")
    return digest


def detail_receipt_hashes(
    details: Sequence[RecordDetail], *, complete: bool
) -> tuple[str | None, str | None]:
    """Return provenance hashes only when the detail set has one truth."""
    if not details or not complete:
        return None, None
    definition_hashes = {
        detail.definition_revision for detail in details if detail.definition_revision
    }
    profile_hashes = {
        detail.profile_revision_hash for detail in details if detail.profile_revision_hash
    }
    definition_hash = next(iter(definition_hashes)) if len(definition_hashes) == 1 else None
    profile_hash = next(iter(profile_hashes)) if len(profile_hashes) == 1 else None
    return definition_hash, profile_hash


logger = logging.getLogger(__name__)


async def commit_resolver_started(
    evidence_ports: BusinessQueryEvidencePorts | None,
    *,
    resolver_query_id: str,
    correlation_id: str,
    value_type: ResolvableType,
    evidence: BusinessQueryEvidenceContext | None,
    step_timeout_seconds: float = 10.0,
) -> Incomplete | None:
    if evidence_ports is None:
        return None
    if evidence is None:
        return Incomplete(reason_code="adapter_invalid")
    start = ResolverQueryStart(
        resolver_query_id=resolver_query_id,
        correlation_id=correlation_id,
        project_id=evidence.project_id,
        value_type=value_type,
        resolver_version=RESOLVER_VERSION,
        started_at=datetime.now(tz=UTC),
    )
    try:
        stored_id = await wait_for(
            evidence_ports.event_store.append_resolver_started(start),
            timeout=step_timeout_seconds,
        )
        if stored_id != resolver_query_id:
            raise RuntimeError("resolver started store returned a different id")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "resolver started append failed correlation_id=%s error=%s",
            correlation_id,
            type(exc).__name__,
        )
        return Incomplete(reason_code="adapter_invalid")
    return None


async def seal_execution_answer(
    evidence_ports: BusinessQueryEvidencePorts,
    answer: UnsealedAdapterAnswer,
    *,
    scoped: ScopedPlan,
    principal: Principal,
    correlation_id: str,
    evidence: BusinessQueryEvidenceContext,
    resolver_query_id: str | None = None,
    step_timeout_seconds: float = 10.0,
    plan_store: Any | None = None,
    pagination_secret: str | None = None,
    mint_page_cursor: bool = True,
    stored_plan_ttl_seconds: int = 14400,
    original_question: str | None = None,
    root_answer_query_id: str | None = None,
    changes: tuple[str, ...] = (),
    continuation_tier: Literal["patch", "planned", "fresh"] | None = "fresh",
    owner_hint: BusinessQueryOwnerHint | None = None,
) -> Answered | Incomplete:
    facts = answer.evidence
    if (
        answer.rows != list(facts.result_rows)
        or answer.total_row_count != facts.total_row_count
        or facts.truncated != (facts.total_row_count > len(facts.result_rows))
    ):
        return Incomplete(reason_code="adapter_invalid")
    sealed_selections: list[Answered] = []
    for selection in answer.selections:
        sealed_selection = await seal_execution_answer(
            evidence_ports,
            selection.answer,
            scoped=selection.scoped,
            principal=principal,
            correlation_id=correlation_id,
            evidence=evidence,
            resolver_query_id=resolver_query_id,
            step_timeout_seconds=step_timeout_seconds,
            plan_store=None,
            mint_page_cursor=False,
            root_answer_query_id=root_answer_query_id,
            continuation_tier=continuation_tier,
        )
        if not isinstance(sealed_selection, Answered):
            return sealed_selection
        sealed_selections.append(sealed_selection)
    answer_query_id = scoped.answer_query_id or answer_query_id_for(evidence.idempotency_key)
    try:
        event = BusinessQueryExecutionEvent.create(
            answer_query_id=answer_query_id,
            root_answer_query_id=root_answer_query_id,
            project_id=evidence.project_id,
            adapter=facts.adapter,
            backend=facts.backend,
            plan_fingerprint=plan_fingerprint(scoped.plan),
            compiled_query_digest=facts.compiled_query_digest,
            parameter_scope_digest=facts.parameter_scope_digest,
            bundle_hash=bare_digest(scoped.bundle_hash or ""),
            manifest_hash=bare_digest(principal.manifest_hash or ""),
            policy_hash=bare_digest(principal.manifest_hash or ""),
            result_members=facts.result_members,
            result_rows=facts.result_rows,
            returned_row_count=len(facts.result_rows),
            total_row_count=facts.total_row_count,
            truncated=facts.truncated,
            database_identity=facts.database_identity,
            started_at=facts.started_at,
            finished_at=facts.finished_at,
            retention_at=evidence.retention_at,
            payload_mode=evidence.payload_mode,
            payload_classification=evidence.payload_classification,
            route=evidence.route,
            provider=evidence.provider,
            deployment=evidence.deployment,
            output_mode=evidence.output_mode,
            effort=evidence.effort,
            input_tokens=evidence.input_tokens,
            output_tokens=evidence.output_tokens,
            correlation_id=correlation_id,
            case_id=evidence.case_id,
            repeat_index=evidence.repeat_index,
            planner_attempt_id=evidence.planner_attempt_id,
            resolver_query_id=resolver_query_id,
            result_completeness=(
                "partial" if answer.failed_detail_families or facts.truncated else "complete"
            ),
            coverage_status=(
                "incomplete" if answer.failed_detail_families else "verified_complete"
            ),
            failed_detail_families=answer.failed_detail_families,
            detail_evidence=build_detail_evidence(
                getattr(answer, "record_details", []),
                project_id=evidence.project_id,
            ),
        )
        stored_id = await wait_for(
            evidence_ports.event_store.append(event),
            timeout=step_timeout_seconds,
        )
        if stored_id != answer_query_id:
            raise RuntimeError("execution event store returned a different id")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "execution event append failed correlation_id=%s error=%s",
            correlation_id,
            type(exc).__name__,
        )
        return Incomplete(reason_code="evidence_unavailable")

    next_page_action: NextPageAction | None = None
    should_mint_cursor = (
        mint_page_cursor
        and facts.pageable
        and (facts.truncated or facts.total_row_count > len(answer.rows))
        # A set plan is answered whole: the envelope writer for set plans is not wired,
        # and a cursor without it would fail to store. Readers already accept versions 2 and 3.
        and not scoped.plan.derived_sets
    )
    from app.business_query.compile.pagination.plan_payload import derived_plan_payload

    derived_payload = (
        derived_plan_payload(scoped, original_question=original_question)
        if scoped.plan.derived_sets
        else None
    )
    should_store_plan = plan_store is not None and (
        scoped.plan.grain in ("entity_rows", "grouped") or should_mint_cursor
    )
    if should_store_plan:
        from app.business_query.compile.pagination import StoredPlan
        from app.business_query.compile.pagination.scope_binding import ScopeBinding
        from app.business_query.seal.action_tokens import ResultPageCursor

        if should_mint_cursor and not pagination_secret:
            raise ValueError("pagination_secret is required to mint a result page cursor")
        created_at = datetime.now(tz=UTC)
        expires_at = created_at + timedelta(seconds=stored_plan_ttl_seconds)
        binding = ScopeBinding.from_principal(
            principal,
            project_id=evidence.project_id,
            bundle_hash=scoped.bundle_hash or "",
        )
        stored = StoredPlan(
            answer_query_id=answer_query_id,
            plan=scoped.plan,
            # The scope-bound hash, because the page executor re-derives exactly this from
            # the stored row: a plan-only hash makes every cursor read as expired.
            plan_fingerprint=scoped_plan_fingerprint(scoped),
            created_at=created_at,
            expires_at=expires_at,
            principal=str(principal.user_id),
            project_id=evidence.project_id,
            entity_id=principal.entity_id,
            department_id=binding.department_id,
            bundle_hash=scoped.bundle_hash or "",
            policy_hash=principal.manifest_hash or "",
            total_row_count=facts.total_row_count,
            forced=scoped.forced,
            response_policy=scoped.response_policy,
            original_question=original_question,
            derived_payload=derived_payload,
            owner_hint=owner_hint,
            display_members_added=tuple(sorted(scoped.display_members_added)),
        )
        try:
            stored_row = await wait_for(
                plan_store.save_plan(stored),
                timeout=step_timeout_seconds,
            )
            if should_mint_cursor:
                secret = pagination_secret
                # Mint from the row that is actually stored, not from the local scope: an
                # overlapping retry keeps the first row, and a cursor bound to anything else
                # would fail closed as cursor_expired.
                cursor = ResultPageCursor.mint(
                    principal=principal,
                    project_id=evidence.project_id,
                    policy_hash=stored_row.policy_hash or "",
                    bundle_hash=stored_row.bundle_hash or "",
                    answer_query_id=stored_row.answer_query_id,
                    plan_fingerprint=stored_row.plan_fingerprint,
                    page_size=len(answer.rows),
                    expires_at=stored_row.expires_at,
                    total_row_count=stored_row.total_row_count,
                    secret=secret,
                )
                next_page_action = NextPageAction(
                    cursor=cursor.encode(),
                    page_size=len(answer.rows),
                    expires_at=stored_row.expires_at,
                )
        except Exception as exc:
            logger.warning("plan store save failed: %s", exc)

    definition_hash, profile_hash = detail_receipt_hashes(
        getattr(answer, "record_details", []),
        complete=not answer.failed_detail_families and not facts.truncated,
    )
    return Answered(
        answer_text=answer.answer_text,
        rows=answer.rows,
        total_row_count=answer.total_row_count,
        record_refs=answer.record_refs,
        record_details=list(getattr(answer, "record_details", [])),
        failed_detail_families=answer.failed_detail_families,
        result_completeness=(
            "partial" if answer.failed_detail_families or facts.truncated else "complete"
        ),
        coverage_status="incomplete" if answer.failed_detail_families else "verified_complete",
        columns=facts.result_columns,
        next_page_action=next_page_action,
        plan=scoped.plan,
        scope_fingerprint=scoped_plan_fingerprint(scoped),
        derived_payload=derived_payload,
        owner_hint=owner_hint,
        row_identity=answer.row_identity,
        receipt=BusinessQueryReceipt(
            answer_query_id=answer_query_id,
            bundle_hash=scoped.bundle_hash or "",
            manifest_hash=principal.manifest_hash or "",
            plan_fingerprint=plan_fingerprint(scoped.plan),
            row_count=len(answer.rows),
            executed_at=facts.finished_at,
            resolver_query_id=resolver_query_id,
            definition_hash=definition_hash,
            profile_hash=profile_hash,
            continuation_tier=continuation_tier,
            changes=tuple(changes),
            root_answer_query_id=root_answer_query_id,
        ),
        companion_answered=tuple(sealed_selections),
    )


async def seal_page_execution_evidence(
    evidence_ports: BusinessQueryEvidencePorts | None,
    answer: UnsealedAdapterAnswer,
    scoped: ScopedPlan,
    principal: Principal,
    cursor: Any,
    idempotency_key: str | None,
    *,
    evidence: BusinessQueryEvidenceContext | None = None,
    step_timeout_seconds: float = 10.0,
) -> Answered | Incomplete:
    if evidence_ports is None:
        return Incomplete(reason_code="adapter_invalid")
    facts = answer.evidence
    answer_query_id = (
        answer_query_id_for(f"{idempotency_key}:{cursor.offset_row_count}")
        if idempotency_key
        else uuid4().hex
    )
    plan_answer_query_id = cursor.plan_answer_query_id or cursor.answer_query_id
    try:
        event = BusinessQueryExecutionEvent.create(
            answer_query_id=answer_query_id,
            root_answer_query_id=plan_answer_query_id,
            project_id=cursor.project_id or "default",
            adapter=facts.adapter,
            backend=facts.backend,
            plan_fingerprint=scoped_plan_fingerprint(scoped),
            compiled_query_digest=facts.compiled_query_digest,
            parameter_scope_digest=facts.parameter_scope_digest,
            bundle_hash=bare_digest(scoped.bundle_hash or ""),
            manifest_hash=bare_digest(principal.manifest_hash or ""),
            policy_hash=bare_digest(principal.manifest_hash or ""),
            result_members=facts.result_members,
            result_rows=facts.result_rows,
            returned_row_count=len(facts.result_rows),
            total_row_count=facts.total_row_count,
            truncated=facts.truncated,
            database_identity=facts.database_identity,
            started_at=facts.started_at,
            finished_at=facts.finished_at,
            retention_at=(
                evidence.retention_at if evidence else datetime.now(tz=UTC) + timedelta(days=90)
            ),
            payload_mode=evidence.payload_mode if evidence else "encrypted",
            payload_classification=(evidence.payload_classification if evidence else "evaluation"),
            correlation_id=cursor.answer_query_id,
            result_completeness="complete",
            coverage_status="verified_complete",
        )
        stored_id = await wait_for(
            evidence_ports.event_store.append(event),
            timeout=step_timeout_seconds,
        )
        if stored_id != answer_query_id:
            raise RuntimeError("execution event store returned a different id")
    except Exception as exc:
        logger.warning("page execution event append failed: %s", exc)
    from app.business_query.compile.pagination.plan_payload import derived_plan_payload

    derived_payload = (
        derived_plan_payload(scoped, original_question=None) if scoped.plan.derived_sets else None
    )
    return Answered(
        answer_text=answer.answer_text,
        rows=answer.rows,
        total_row_count=answer.total_row_count,
        record_refs=answer.record_refs,
        record_details=list(getattr(answer, "record_details", [])),
        columns=facts.result_columns,
        plan=scoped.plan,
        scope_fingerprint=scoped_plan_fingerprint(scoped),
        derived_payload=derived_payload,
        owner_hint=None,
        receipt=BusinessQueryReceipt(
            answer_query_id=answer_query_id,
            root_answer_query_id=plan_answer_query_id,
            bundle_hash=scoped.bundle_hash or "",
            manifest_hash=principal.manifest_hash or "",
            plan_fingerprint=scoped_plan_fingerprint(scoped),
            row_count=len(answer.rows),
            executed_at=facts.finished_at,
            continuation_tier="fresh",
            changes=(),
        ),
    )
