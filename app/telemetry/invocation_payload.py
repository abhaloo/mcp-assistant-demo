"""Encrypted serialization, bounded metadata extraction,
and terminal evidence for model invocations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.crypto.aead import decrypt_bytes, encrypt_bytes
from app.crypto.event_keyring import EventEncryptionKeyring
from app.services.ask_deadline import Deadline

_logger = logging.getLogger(__name__)

# Max string lengths and keys for bounded metadata
_MAX_META_STRING_LEN = 256
_ALLOWED_META_KEYS = frozenset(
    {
        "model_name",
        "model",
        "finish_reason",
        "stop_reason",
        "system_fingerprint",
        "id",
        "token_usage",
        "usage",
    }
)


class PayloadEncryptionError(RuntimeError):
    """Raised when payload encryption fails. Contains no sensitive content."""


class PayloadDecryptionError(RuntimeError):
    """Raised when payload decryption fails. Contains no sensitive content."""


class TerminalEvidenceError(RuntimeError):
    """Raised when required model invocation evidence is missing or invalid."""


@dataclass(frozen=True)
class ExecutionIdentity:
    """Identity tying model invocations to a specific turn or operation."""

    correlation_id: str
    thread_id: str | None = None
    exchange_id: str | None = None


@dataclass(frozen=True)
class InvocationExpectation:
    """Expected invocations for a terminal execution boundary."""

    min_invocations: int = 1
    expected_purposes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TerminalEvidenceReceipt:
    """Verified receipt of durable trace evidence for terminal commit."""

    correlation_id: str
    invocation_count: int
    evidence_digest: str
    verified_at: datetime


# Thread-safe in-memory fast-path cache of recorded model invocations. This
# is a cache only -- `require_terminal_evidence` treats the durable ledger as
# the source of truth once it is configured, and never lets this cache alone
# satisfy the barrier in that case.
_MAX_RECORDED_EVIDENCE_CORRELATIONS: int = 1000
_evidence_lock = threading.Lock()
_recorded_evidence: dict[str, list[Any]] = {}


def record_evidence_invocation(correlation_id: str, record: Any) -> None:
    """Record an invocation record into the evidence registry."""
    if not correlation_id:
        return
    with _evidence_lock:
        if correlation_id not in _recorded_evidence:
            if len(_recorded_evidence) >= _MAX_RECORDED_EVIDENCE_CORRELATIONS:
                oldest = next(iter(_recorded_evidence))
                _recorded_evidence.pop(oldest, None)
            _recorded_evidence[correlation_id] = []
        _recorded_evidence[correlation_id].append(record)


def get_recorded_evidence(correlation_id: str) -> list[Any]:
    """Retrieve recorded invocations for a given correlation ID."""
    with _evidence_lock:
        return list(_recorded_evidence.get(correlation_id, []))


def clear_recorded_evidence_for_tests() -> None:
    """Clear recorded evidence map (used between test runs)."""
    with _evidence_lock:
        _recorded_evidence.clear()


@dataclass(frozen=True)
class InvocationPayload:
    """Plaintext representation of model invocation data."""

    request_messages: list[dict[str, Any]] | str
    response_content: str | None
    reasoning_content: str | None = None
    raw_response_metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class EncryptedInvocationPayload:
    """Encrypted envelope for model invocation data."""

    key_version: str
    nonce: str
    ciphertext: str
    digest: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "key_version": self.key_version,
                "nonce": self.nonce,
                "ciphertext": self.ciphertext,
                "digest": self.digest,
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> EncryptedInvocationPayload:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise TypeError("payload json must be a dict")
            return cls(
                key_version=str(data["key_version"]),
                nonce=str(data["nonce"]),
                ciphertext=str(data["ciphertext"]),
                digest=str(data["digest"]),
            )
        except Exception as exc:
            raise PayloadDecryptionError("invalid encrypted payload json envelope") from exc


def extract_bounded_response_metadata(response: Any) -> dict[str, Any]:
    """Extract safe, bounded metadata dictionary from LLM response or result."""
    if response is None:
        return {}

    raw_meta: dict[str, Any] = {}
    message = None
    if hasattr(response, "generations") and response.generations:
        first_gen = response.generations[0]
        if first_gen:
            gen = first_gen[0]
            message = getattr(gen, "message", None)

    if message is not None:
        raw_meta.update(getattr(message, "response_metadata", {}) or {})
    elif hasattr(response, "response_metadata"):
        raw_meta.update(getattr(response, "response_metadata", {}) or {})
    elif isinstance(response, dict):
        raw_meta.update(response)

    bounded: dict[str, Any] = {}
    for key, val in raw_meta.items():
        if key not in _ALLOWED_META_KEYS:
            continue
        if isinstance(val, str):
            bounded[key] = val[:_MAX_META_STRING_LEN]
        elif isinstance(val, (int, float, bool)) or val is None:
            bounded[key] = val
        elif isinstance(val, dict):
            bounded[key] = {
                str(k): (v[:_MAX_META_STRING_LEN] if isinstance(v, str) else v)
                for k, v in val.items()
                if isinstance(v, (int, float, bool, str)) or v is None
            }

    return bounded


def serialize_payload(payload: InvocationPayload) -> bytes:
    """Serialize InvocationPayload to canonical JSON bytes."""
    data: dict[str, Any] = {
        "request_messages": payload.request_messages,
        "response_content": payload.response_content,
    }
    if payload.reasoning_content is not None:
        data["reasoning_content"] = payload.reasoning_content
    if payload.raw_response_metadata is not None:
        data["raw_response_metadata"] = payload.raw_response_metadata

    return json.dumps(data, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")


def deserialize_payload(data_bytes: bytes) -> InvocationPayload:
    """Deserialize JSON bytes into InvocationPayload."""
    try:
        data = json.loads(data_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise TypeError("deserialized payload must be a dictionary")

        return InvocationPayload(
            request_messages=data.get("request_messages", "[]"),
            response_content=data.get("response_content"),
            reasoning_content=data.get("reasoning_content"),
            raw_response_metadata=data.get("raw_response_metadata"),
        )
    except Exception as exc:
        raise PayloadDecryptionError("failed to deserialize invocation payload") from exc


def compute_payload_digest(data_bytes: bytes) -> str:
    """Compute sha256 hex digest of serialized bytes."""
    return hashlib.sha256(data_bytes).hexdigest()


def encrypt_invocation_payload(
    keyring: EventEncryptionKeyring,
    payload: InvocationPayload,
    *,
    aad: bytes | None = None,
) -> EncryptedInvocationPayload:
    """Encrypt InvocationPayload using AEAD keyring and return EncryptedInvocationPayload."""
    try:
        serialized = serialize_payload(payload)
        digest = compute_payload_digest(serialized)
        key_version, nonce, ciphertext = encrypt_bytes(keyring, serialized, aad=aad)
        return EncryptedInvocationPayload(
            key_version=key_version,
            nonce=base64.b64encode(nonce).decode("ascii"),
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
            digest=digest,
        )
    except Exception as exc:
        _logger.warning("payload encryption failed: %s", type(exc).__name__)
        raise PayloadEncryptionError("failed to encrypt invocation payload") from exc


def decrypt_invocation_payload(
    keyring: EventEncryptionKeyring,
    encrypted: EncryptedInvocationPayload | str,
    *,
    aad: bytes | None = None,
) -> InvocationPayload:
    """Decrypt EncryptedInvocationPayload using AEAD keyring and return InvocationPayload."""
    try:
        if isinstance(encrypted, str):
            envelope = EncryptedInvocationPayload.from_json(encrypted)
        else:
            envelope = encrypted

        nonce_bytes = base64.b64decode(envelope.nonce, validate=True)
        ciphertext_bytes = base64.b64decode(envelope.ciphertext, validate=True)
        plaintext_bytes = decrypt_bytes(
            keyring, envelope.key_version, nonce_bytes, ciphertext_bytes, aad=aad
        )
        return deserialize_payload(plaintext_bytes)
    except PayloadDecryptionError:
        raise
    except Exception as exc:
        _logger.warning("payload decryption failed: %s", type(exc).__name__)
        raise PayloadDecryptionError("failed to decrypt invocation payload") from exc


async def require_terminal_evidence(
    execution: ExecutionIdentity,
    expected_invocations: InvocationExpectation,
    deadline: Deadline | None = None,
) -> TerminalEvidenceReceipt:
    """Require verified durable evidence of model invocations before terminal commit.

    The in-memory map above is a fast-path cache only. Once the invocation
    ledger store is configured, the durable ``model_invocations`` rows for
    this correlation ID are the count this barrier checks against -- a
    populated cache can never satisfy it on its own when the durable store
    disagrees, and an unreachable durable store fails closed rather than
    falling back to the cache.
    """
    cid = execution.correlation_id
    if not cid:
        raise TerminalEvidenceError("missing correlation_id for terminal evidence requirement")

    if deadline is not None and deadline.is_expired:
        raise TerminalEvidenceError(
            f"deadline expired before terminal evidence could be verified for correlation {cid}"
        )

    # Deferred import: app.telemetry.invocation_ledger imports this module at
    # module load time, so a top-level import here would be circular.
    from app.telemetry import invocation_ledger

    records: Sequence[Any]
    if invocation_ledger.ledger_store_configured():
        try:
            await invocation_ledger.flush_ledger_writes()
            records = await invocation_ledger.query_durable_invocation_evidence(cid)
        except Exception as exc:
            _logger.warning(
                "durable evidence lookup failed for terminal commit: %s", type(exc).__name__
            )
            raise TerminalEvidenceError(
                f"durable evidence store unreachable for correlation {cid}"
            ) from None
    else:
        records = get_recorded_evidence(cid)

    if len(records) < expected_invocations.min_invocations:
        raise TerminalEvidenceError(
            f"insufficient model invocation evidence for correlation {cid}: "
            f"found {len(records)}, required {expected_invocations.min_invocations}"
        )

    # Compute aggregate digest across records
    digests = []
    for r in records:
        d = getattr(r, "payload_digest", None)
        if not d:
            resp = getattr(r, "response_content", "") or ""
            d = hashlib.sha256(resp.encode()).hexdigest()
        digests.append(d)

    agg_digest = hashlib.sha256(":".join(digests).encode()).hexdigest()
    return TerminalEvidenceReceipt(
        correlation_id=cid,
        invocation_count=len(records),
        evidence_digest=agg_digest,
        verified_at=datetime.now(UTC),
    )
