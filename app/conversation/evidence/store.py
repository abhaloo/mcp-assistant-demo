"""Encrypted storage for Ask evidence snapshots.

Follows Spec §7 and ADR 0076.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.conversation.evidence.contracts import (
    MAX_SNAPSHOT_BYTES,
    EvidenceSnapshot,
    SnapshotBindings,
    SnapshotPayload,
    SnapshotTooLargeError,
)
from app.conversation.evidence.model import AskEvidenceSnapshot
from app.crypto.aead import decrypt_bytes, encrypt_bytes

if TYPE_CHECKING:
    from app.crypto.event_keyring import EventEncryptionKeyring

logger = logging.getLogger(__name__)

# The newest payload schema this reader knows. A field that changes what a
# restore may release bumps the writer's version, so an older reader refuses it.
_READABLE_SCHEMA_VERSION = 1


def canonical_binding_bytes(bound: SnapshotBindings) -> bytes:
    """Canonical UTF-8 JSON representation of snapshot bindings used as AEAD AAD."""
    return json.dumps(
        bound.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class SqlAlchemyEvidenceSnapshotStore:
    """Stores and retrieves encrypted evidence snapshots in PostgreSQL."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        keyring: EventEncryptionKeyring,
    ) -> None:
        self._session_factory = session_factory
        self._keyring = keyring

    async def put(
        self,
        bindings: SnapshotBindings,
        payload: SnapshotPayload,
        *,
        restore_ref: str | None = None,
    ) -> str:
        """Serialize, digest, encrypt, and persist an immutable evidence snapshot.

        Raises:
            SnapshotTooLargeError: If serialized plaintext exceeds 1 MiB.
        """
        plain = payload.model_dump_json().encode("utf-8")
        if len(plain) > MAX_SNAPSHOT_BYTES:
            raise SnapshotTooLargeError(
                f"snapshot exceeds limit ({len(plain)} > {MAX_SNAPSHOT_BYTES} bytes)"
            )

        digest = hashlib.sha256(plain).hexdigest()
        bound = bindings.model_copy(update={"content_digest": digest})
        aad = canonical_binding_bytes(bound)

        key_version, nonce, ciphertext = encrypt_bytes(self._keyring, plain, aad=aad)

        ref = restore_ref or secrets.token_urlsafe(32)
        if len(ref) != 43:
            raise ValueError(f"restore_ref must be exactly 43 characters, got {len(ref)}")

        # Normalise key_version to int for PostgreSQL Integer column
        key_ver_int = (
            int(str(key_version).lstrip("v")) if isinstance(key_version, str) else int(key_version)
        )

        row = AskEvidenceSnapshot(
            restore_ref=ref,
            actor_id=bound.actor_id,
            entity_id=bound.entity_id,
            department_id=bound.department_id,
            thread_id=bound.thread_id,
            run_id=bound.run_id,
            exchange_id=bound.exchange_id,
            schema_version=bound.schema_version,
            content_digest=digest,
            key_version=key_ver_int,
            nonce=nonce,
            ciphertext=ciphertext,
            created_at=bound.created_at,
            expires_at=bound.expires_at,
            tombstone=bound.tombstone,
        )

        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

        return ref

    async def get(self, restore_ref: str) -> EvidenceSnapshot | None:
        """Fetch, decrypt, and verify an evidence snapshot by restore_ref."""
        stmt = select(AskEvidenceSnapshot).where(AskEvidenceSnapshot.restore_ref == restore_ref)
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()

        if row is None:
            return None
        if row.schema_version > _READABLE_SCHEMA_VERSION:
            logger.warning(
                "Snapshot schema_version %s is newer than this reader for restore_ref %s",
                row.schema_version,
                restore_ref,
            )
            return None

        bound = SnapshotBindings(
            project="default",
            actor_id=row.actor_id,
            entity_id=row.entity_id,
            department_id=row.department_id,
            thread_id=row.thread_id,
            run_id=row.run_id,
            exchange_id=row.exchange_id,
            schema_version=row.schema_version,
            content_digest=row.content_digest,
            created_at=row.created_at,
            expires_at=row.expires_at,
            tombstone=row.tombstone,
        )

        aad = canonical_binding_bytes(bound)

        key_ver_str = str(row.key_version)
        if (
            hasattr(self._keyring, "_keys")
            and key_ver_str not in self._keyring._keys
            and f"v{key_ver_str}" in self._keyring._keys
        ):
            key_ver_str = f"v{key_ver_str}"

        try:
            plain = decrypt_bytes(self._keyring, key_ver_str, row.nonce, row.ciphertext, aad=aad)
        except Exception as exc:
            logger.warning("Decryption failed for restore_ref %s: %s", restore_ref, exc)
            return None

        if hashlib.sha256(plain).hexdigest() != row.content_digest:
            logger.warning("Digest mismatch for restore_ref %s", restore_ref)
            return None

        payload = _read_payload(plain, restore_ref)
        if payload is None:
            return None

        return EvidenceSnapshot(
            restore_ref=row.restore_ref,
            bindings=bound,
            payload=payload,
        )


def _payload_without(
    plain: bytes, unknown: list[tuple[int | str, ...]], restore_ref: str
) -> SnapshotPayload | None:
    """Drop the fields this reader does not know, then validate the rest."""
    fields = sorted(".".join(str(part) for part in loc) for loc in unknown)
    data = json.loads(plain)
    try:
        for *path, field in unknown:
            node = data
            for part in path:
                node = node[part]
            node.pop(field)
    except (KeyError, IndexError, TypeError):
        # A location through a union member names the member, not a key.
        logger.warning(
            "Snapshot fields this reader does not know could not be dropped for restore_ref %s: %s",
            restore_ref,
            fields,
        )
        return None
    logger.warning(
        "Snapshot fields this reader does not know were dropped for restore_ref %s: %s",
        restore_ref,
        fields,
    )
    try:
        # Strict models take arrays and date strings only from JSON, so validate JSON.
        return SnapshotPayload.model_validate_json(json.dumps(data))
    except ValidationError as exc:
        logger.warning(
            "Snapshot payload validation failed for restore_ref %s: %s", restore_ref, exc
        )
        return None


def _read_payload(plain: bytes, restore_ref: str) -> SnapshotPayload | None:
    """The payload, keeping what this reader knows when a newer writer added a field.

    A rolled-back reader restores the turn instead of blanking it. Any error other
    than an unknown field refuses the payload.
    """
    try:
        return SnapshotPayload.model_validate_json(plain)
    except ValidationError as exc:
        unknown = [
            tuple(error["loc"]) for error in exc.errors() if error["type"] == "extra_forbidden"
        ]
        if len(unknown) != exc.error_count():
            logger.warning(
                "Snapshot payload validation failed for restore_ref %s: %s", restore_ref, exc
            )
            return None
    return _payload_without(plain, unknown, restore_ref)


# Export canonical alias
EvidenceSnapshotStore = SqlAlchemyEvidenceSnapshotStore
