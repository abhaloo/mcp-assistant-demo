"""Composition root factory functions for evidence retention and restore."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import settings
from app.conversation.evidence.ports import (
    EvidenceRestoreService as EvidenceRestoreServicePort,
)
from app.conversation.evidence.ports import (
    EvidenceSnapshotStore as EvidenceSnapshotStorePort,
)
from app.conversation.evidence.ports import (
    RetainedScopeAuthorizer as RetainedScopeAuthorizerPort,
)
from app.conversation.evidence.scope_authorizer import RetainedScopeAuthorizer
from app.conversation.evidence.service import EvidenceRestoreService
from app.conversation.evidence.store import SqlAlchemyEvidenceSnapshotStore
from app.crypto.event_keyring import EventEncryptionKeyring

if TYPE_CHECKING:
    from app.resources import ProcessResources


def build_evidence_snapshot_keyring(
    raw_keys: str | None = None,
) -> EventEncryptionKeyring:
    keys = raw_keys or settings.business_query_event_encryption_keys
    if not keys:
        raise RuntimeError("business_query_event_encryption_keys is not configured")
    return EventEncryptionKeyring.parse(keys)


def build_evidence_snapshot_store(
    resources: ProcessResources,
    *,
    keyring: EventEncryptionKeyring | None = None,
) -> EvidenceSnapshotStorePort:
    kr = keyring or build_evidence_snapshot_keyring()
    return SqlAlchemyEvidenceSnapshotStore(
        session_factory=resources.query_record_session_factory,
        keyring=kr,
    )


def build_retained_scope_authorizer() -> RetainedScopeAuthorizerPort:
    return RetainedScopeAuthorizer()


def build_evidence_restore_service(
    resources: ProcessResources,
    *,
    store: EvidenceSnapshotStorePort | None = None,
    scope_authorizer: RetainedScopeAuthorizerPort | None = None,
    keyring: EventEncryptionKeyring | None = None,
) -> EvidenceRestoreServicePort:
    ev_store = store or build_evidence_snapshot_store(resources, keyring=keyring)
    authorizer = scope_authorizer or build_retained_scope_authorizer()
    return EvidenceRestoreService(store=ev_store, scope_authorizer=authorizer)
