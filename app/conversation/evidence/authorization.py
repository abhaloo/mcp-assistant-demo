"""Authorize a retained evidence snapshot under the current principal (spec §7).

Never invokes a planner, generator, or replacement document search. Every
check either passes or names why the snapshot is withheld; a snapshot with a
dependency that cannot be verified is withheld as a whole.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from app.auth import Principal
from app.conversation.evidence.contracts import EvidenceSnapshot, RestoredEvidence
from app.rag.access_tiers import document_tiers_for

if TYPE_CHECKING:
    from app.conversation.evidence.ports import RetainedScopeAuthorizer

DenialReason = Literal["expired", "policy_denied", "dependency_unverifiable"]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _identity_denial(snapshot: EvidenceSnapshot, principal: Principal) -> DenialReason | None:
    same_actor = (
        str(principal.user_id) == snapshot.actor_id
        and principal.entity_id == snapshot.entity_id
        and principal.department_id == snapshot.department_id
    )
    return None if same_actor and not snapshot.tombstone else "policy_denied"


def _business_query_denial(
    snapshot: EvidenceSnapshot,
    principal: Principal,
    scope_authorizer: RetainedScopeAuthorizer,
) -> DenialReason | None:
    payload = snapshot.payload
    bq = payload.restored_turn.business_query
    if bq is not None:
        if not payload.stored_plans:
            return "dependency_unverifiable"
        if len(payload.stored_plans) > 2 or len(bq.envelopes) > 2:
            return "dependency_unverifiable"
        if len(payload.stored_plans) != len(bq.envelopes):
            return "dependency_unverifiable"
        if any(
            stored.answer_query_id != env.answer_query_id
            for stored, env in zip(payload.stored_plans, bq.envelopes, strict=False)
        ):
            return "dependency_unverifiable"

    if payload.stored_plans and not scope_authorizer.authorize_retained_scope(snapshot, principal):
        return "policy_denied"
    return None


def _document_denial(snapshot: EvidenceSnapshot, principal: Principal) -> DenialReason | None:
    sources = snapshot.payload.restored_turn.sources
    provenance = snapshot.payload.document_provenance
    doc_sources = [s for s in sources if s.resource_type is None and s.record_id is None]
    if doc_sources and not provenance:
        return "dependency_unverifiable"
    prov_ids = {prov.source_id for prov in provenance if prov.source_id}
    if any(s.id not in prov_ids for s in doc_sources):
        return "dependency_unverifiable"
    if any(not prov.source_id or not prov.access_tier for prov in provenance):
        return "dependency_unverifiable"
    allowed = set(document_tiers_for(principal))
    if any(prov.access_tier not in allowed for prov in provenance):
        return "policy_denied"
    return None


def _dependency_denial(snapshot: EvidenceSnapshot, principal: Principal) -> DenialReason | None:
    deps = snapshot.payload.narrative_dependencies
    sources = snapshot.payload.restored_turn.sources
    if deps is None:
        record_sources = any(
            s.resource_type is not None or s.record_id is not None for s in sources
        )
        return "dependency_unverifiable" if record_sources else None
    if deps.has_history and not deps.history_turn_ids:
        return "dependency_unverifiable"
    if deps.record_context_digest is not None and (
        principal.record_context_digest != deps.record_context_digest
    ):
        return "policy_denied"
    return None


def authorize_snapshot(
    snapshot: EvidenceSnapshot,
    principal: Principal,
    *,
    now: datetime,
    scope_authorizer: RetainedScopeAuthorizer,
) -> RestoredEvidence:
    """The restore verdict for one snapshot: authorized with its payload, or a denial."""
    if _as_utc(now) >= _as_utc(snapshot.expires_at):
        denial: DenialReason | None = "expired"
    else:
        denial = (
            _identity_denial(snapshot, principal)
            or _business_query_denial(snapshot, principal, scope_authorizer)
            or _document_denial(snapshot, principal)
            or _dependency_denial(snapshot, principal)
        )
    if denial is not None:
        return RestoredEvidence(
            restore_ref=snapshot.restore_ref, status="denied", reason=denial, payload=None
        )
    return RestoredEvidence(
        restore_ref=snapshot.restore_ref,
        status="authorized",
        reason="authorized",
        payload=snapshot.payload.restored_turn,
    )
