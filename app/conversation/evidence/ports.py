"""Ports and protocols for evidence retention, storage, and reauthorization.

Follows Spec §7 and ADR 0076.
"""

from __future__ import annotations

from typing import Protocol

from app.auth import Principal
from app.conversation.evidence.contracts import (
    EvidenceSnapshot,
    RestoreRequest,
    RestoreResponse,
    SnapshotBindings,
    SnapshotPayload,
)


class EvidenceSnapshotStore(Protocol):
    """Storage port for encrypted evidence snapshots."""

    async def put(
        self,
        bindings: SnapshotBindings,
        payload: SnapshotPayload,
        *,
        restore_ref: str | None = None,
    ) -> str: ...

    async def get(self, restore_ref: str) -> EvidenceSnapshot | None: ...


class RetainedScopeAuthorizer(Protocol):
    """Domain scope authorizer port for historical evidence restore."""

    def authorize_retained_scope(
        self,
        snapshot: EvidenceSnapshot,
        principal: Principal,
    ) -> bool: ...


class EvidenceRestoreService(Protocol):
    """Restore orchestration port for historical evidence."""

    async def restore(
        self,
        request: RestoreRequest,
        principal: Principal,
    ) -> RestoreResponse: ...
