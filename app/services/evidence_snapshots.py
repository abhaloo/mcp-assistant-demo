"""Retain a finished version 1 turn as an evidence snapshot (spec §7).

The snapshot is written after the answer is committed and before the transcript
row that carries its ``restore_ref``. Anything that cannot be bound to policy
(a BQ answer without a plan, a multi-envelope answer, a source without a tier)
is left out entirely: the live answer stays usable and ``restore_ref`` is null.
A query that did not answer carries no plan, so its turn keeps a text-only snapshot.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

from app.auth import Principal
from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.outcomes import BusinessQueryWireOutcome
from app.config import settings
from app.conversation.evidence.composition import build_evidence_snapshot_store
from app.conversation.evidence.contracts import (
    NO_TIMELINE,
    NarrativeDependencies,
    SnapshotPayload,
    TurnTimeline,
)
from app.rag.retrieval.document_contracts import DocumentProvenance
from app.rag.retrieval.passages import RetrievedSource, to_json_source
from app.resources import current_process_resources
from app.services.ask_outcome import TurnEvidence
from app.services.ask_result_projection import (
    build_snapshot_bindings,
    build_snapshot_payload,
    publish_evidence_snapshot,
)
from app.services.retained_evidence import MAX_TURN_TABLES, RetentionMismatchError, to_stored_plan
from app.telemetry.correlation import current_restore_ref

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = timedelta(hours=24)

# The stream binds its sink's reader; the snapshot written inside the answer path
# reads the steps and thought rows the person saw. Context vars are task-local.
_turn_timeline: ContextVar[Callable[[], TurnTimeline] | None] = ContextVar(
    "turn_timeline", default=None
)


def bind_turn_timeline(reader: Callable[[], TurnTimeline] | None) -> None:
    """Bind the reader of this turn's timeline for the task's context."""
    _turn_timeline.set(reader)


def current_turn_timeline() -> TurnTimeline:
    """The bound turn's timeline, read now; empty when no stream bound one."""
    reader = _turn_timeline.get()
    return reader() if reader is not None else NO_TIMELINE


def _not_retained(gate: str) -> None:
    """Name the gate that kept this turn out of the snapshot. The answer is
    unaffected; a null restore_ref can now be traced to its cause. Logged at
    warning level because deployments run the app logger at warning."""
    logger.warning("evidence snapshot not retained: %s", gate)


def _stored_plans(
    evidence: TurnEvidence, *, principal: Principal, now: datetime
) -> tuple[StoredPlan, ...] | None:
    """The scope bindings a BQ answer needs at restore; None when they cannot be built."""
    outcome: BusinessQueryWireOutcome | None = evidence.business_query
    if outcome is None:
        if evidence.retained_members:
            return _not_retained("retained_members_without_outcome")
        return ()

    envelopes = outcome.envelopes
    if outcome.outcome != "answered" and not envelopes and not evidence.retained_members:
        # A refused or clarified query shows text only: there is no plan to re-scope.
        return ()
    if len(envelopes) == 0 or len(envelopes) > MAX_TURN_TABLES:
        return _not_retained("envelope_count")

    if evidence.retained_members:
        members = evidence.retained_members
        if len(members) != len(envelopes):
            return _not_retained("member_envelope_count")
        stored: list[StoredPlan] = []
        for i, (m, env) in enumerate(zip(members, envelopes, strict=False)):
            if m.ordinal != i + 1 or m.answer_query_id != env.answer_query_id:
                return _not_retained("member_envelope_mismatch")
            try:
                sp = to_stored_plan(
                    m,
                    created_at=now,
                    expires_at=now + SNAPSHOT_TTL,
                    principal=str(principal.user_id),
                    project_id="default",
                    entity_id=principal.entity_id,
                    department_id=principal.department_id,
                    bundle_hash=env.bundle_hash,
                    policy_hash=principal.manifest_hash,
                    total_row_count=env.total_row_count,
                    response_policy=evidence.response_policy,
                )
                stored.append(sp)
            except RetentionMismatchError:
                return _not_retained("retention_mismatch")
        return tuple(stored)

    return _not_retained("no_retained_members")


def _provenance(sources: tuple[RetrievedSource, ...]) -> tuple[DocumentProvenance, ...]:
    return tuple(
        DocumentProvenance(
            source_id=src.id,
            access_tier=src.access_tier,
            content_hash=None,
            chunk_id=None,
            ingest_run_id=None,
            index_name=None,
            chunk_index=src.chunk_index,
        )
        for src in sources
    )


def snapshot_payload(
    evidence: TurnEvidence,
    *,
    principal: Principal,
    thread_id: str,
    run_id: str,
    exchange_id: str,
    now: datetime,
    timeline: TurnTimeline = NO_TIMELINE,
) -> SnapshotPayload | None:
    """Everything a restore may release for this turn; None when it cannot be bound."""
    stored_plans = _stored_plans(evidence, principal=principal, now=now)
    if stored_plans is None:
        return None

    # Exclude record-context sources from document tier check
    doc_sources = tuple(
        src for src in evidence.sources if src.resource_type is None and src.record_id is None
    )
    if any(src.access_tier is None for src in doc_sources):
        return _not_retained("source_without_tier")

    if evidence.document_provenance:
        prov = evidence.document_provenance
        if any(not p.source_id or not p.access_tier for p in prov):
            return _not_retained("provenance_incomplete")
        prov_ids = {p.source_id for p in prov}
        if any(src.id not in prov_ids for src in doc_sources):
            return _not_retained("provenance_source_mismatch")
        doc_prov = prov
    else:
        doc_prov = _provenance(doc_sources)

    # Validate citations if present
    if evidence.citations and evidence.citations.cited:
        source_ids = {s.id for s in evidence.sources if s.id}
        if any(c.id not in source_ids for c in evidence.citations.cited):
            return _not_retained("citation_not_in_sources")

    return build_snapshot_payload(
        thread_id=thread_id,
        run_id=run_id,
        exchange_id=exchange_id,
        answer_text=evidence.answer_text,
        turn_result=evidence.turn_result,
        sources=[to_json_source(src) for src in evidence.sources],
        citations=evidence.citations,
        business_query=evidence.business_query,
        presentation=evidence.presentation,
        stored_plans=stored_plans,
        document_provenance=doc_prov,
        narrative_dependencies=NarrativeDependencies(
            record_context_digest=principal.record_context_digest,
            source_restore_refs=evidence.source_restore_refs,
        ),
        duration_ms=timeline.duration_ms,
        steps=timeline.steps,
        thoughts=timeline.thoughts,
        follow_ups=evidence.follow_ups,
        unanswered_part=evidence.unanswered_part,
    )


def snapshot_store_available() -> bool:
    """Retention needs the Query Record store and the evidence keyring."""
    return bool(
        settings.query_record_database_url.strip()
        and settings.business_query_event_encryption_keys.strip()
    )


async def publish_turn_evidence(
    evidence: TurnEvidence,
    *,
    principal: Principal,
    thread_id: str,
    run_id: str,
    exchange_id: str,
) -> str | None:
    """Write the snapshot and return its restore_ref, or None when nothing is retained.

    Nothing here may fail the answer that was already committed: any assembly
    or storage error logs its type and leaves restore_ref null.
    """
    if not snapshot_store_available():
        return None
    now = datetime.now(tz=UTC)
    try:
        timeline = current_turn_timeline()
    except Exception as exc:  # noqa: BLE001 - the steps are optional; the text is the snapshot
        logger.warning("turn timeline not read: %s", type(exc).__name__)
        timeline = NO_TIMELINE
    try:
        payload = snapshot_payload(
            evidence,
            principal=principal,
            thread_id=thread_id,
            run_id=run_id,
            exchange_id=exchange_id,
            now=now,
            timeline=timeline,
        )
        if payload is None:
            return None
        bindings = build_snapshot_bindings(
            principal=principal,
            thread_id=thread_id,
            run_id=run_id,
            exchange_id=exchange_id,
            now=now,
        )
        store = build_evidence_snapshot_store(current_process_resources())
    except Exception as exc:  # noqa: BLE001 - a snapshot failure never fails the answer
        logger.warning("evidence snapshot not assembled: %s", type(exc).__name__)
        return None
    # The answer path publishes under the attempt reference the stream minted;
    # with no bound reference (legacy callers), the store mints one as before.
    return await publish_evidence_snapshot(
        store, bindings, payload, restore_ref=current_restore_ref()
    )
