"""EvidenceRestoreService orchestrating snapshot retrieval and reauthorization.

Follows Spec §7 and ADR 0076.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.auth import Principal
from app.conversation.evidence.authorization import authorize_snapshot
from app.conversation.evidence.contracts import (
    EvidenceSnapshot,
    RestoredEvidence,
    RestoreRequest,
    RestoreResponse,
)
from app.conversation.evidence.ports import EvidenceSnapshotStore, RetainedScopeAuthorizer

logger = logging.getLogger(__name__)


class EvidenceRestoreService:
    """Orchestrates fetching and reauthorizing evidence without LLM/planner invocation."""

    def __init__(
        self,
        store: EvidenceSnapshotStore,
        scope_authorizer: RetainedScopeAuthorizer,
    ) -> None:
        self._store = store
        self._scope_authorizer = scope_authorizer

    async def restore(
        self,
        request: RestoreRequest,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> RestoreResponse:
        """Evaluate each requested restore reference against current permissions."""
        eval_time = now or datetime.now(tz=UTC)
        results: list[RestoredEvidence] = []

        for ref_item in request.references:
            ref = ref_item.restore_ref
            try:
                snapshot = await self._store.get(ref)
            except Exception as exc:
                logger.warning("Error reading evidence snapshot %s: %s", ref, exc)
                snapshot = None

            if snapshot is None:
                results.append(RestoredEvidence.unavailable(ref))
                continue

            # Thread mismatch: reference cannot be restored outside its originating thread
            if snapshot.thread_id != request.thread_id:
                results.append(
                    RestoredEvidence(
                        restore_ref=ref,
                        status="denied",
                        reason="invalid_reference",
                        payload=None,
                    )
                )
                continue

            verdict = authorize_snapshot(
                snapshot,
                principal,
                now=eval_time,
                scope_authorizer=self._scope_authorizer,
            )
            if verdict.status == "authorized":
                verdict = await self._with_dependencies(
                    snapshot, verdict, request.thread_id, principal, eval_time
                )
            results.append(verdict)

        return RestoreResponse(version=1, results=tuple(results))

    async def _with_dependencies(
        self,
        snapshot: EvidenceSnapshot,
        verdict: RestoredEvidence,
        thread_id: str,
        principal: Principal,
        eval_time: datetime,
    ) -> RestoredEvidence:
        deps = snapshot.payload.narrative_dependencies
        refs = deps.source_restore_refs if deps else ()
        denied = RestoredEvidence(
            restore_ref=snapshot.restore_ref,
            status="denied",
            reason="dependency_unverifiable",
            payload=None,
        )
        for dep_ref in refs:
            try:
                dep_snap = await self._store.get(dep_ref)
            except Exception as exc:
                logger.warning("Error reading dependency snapshot %s: %s", dep_ref, exc)
                dep_snap = None
            if dep_snap is None or dep_snap.thread_id != thread_id:
                return denied
            child_deps = dep_snap.payload.narrative_dependencies
            if child_deps and child_deps.source_restore_refs:
                return denied
            dep_verdict = authorize_snapshot(
                dep_snap, principal, now=eval_time, scope_authorizer=self._scope_authorizer
            )
            if dep_verdict.status != "authorized":
                return denied
        return verdict
