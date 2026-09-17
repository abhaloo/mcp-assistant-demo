"""HMAC-signed single-use continuation tokens for structured-only fallback."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidTag

from app.auth import Principal
from app.config import settings
from app.conversation import transcript_store
from app.conversation.transcript_store import (
    CONTINUATION_LEASE_SECONDS,
    ContinuationJoinResult,
    ConversationStore,
)
from app.conversation.transcript_store import (
    ContinuationClaim as StoreContinuationClaim,
)
from app.core.errors import (
    ContinuationClaimRejectedError,
    KeyringConfigurationError,
    NotFoundError,
)
from app.crypto.aead import decrypt_bytes, encrypt_bytes
from app.crypto.event_keyring import EventEncryptionKeyring

if TYPE_CHECKING:
    from app.resources import ProcessResources
    from app.services.ask_deadline import Deadline

_DEFAULT_TTL_S = 300  # 5 minutes
_lock = threading.Lock()
_CONSUMED_JTIS: dict[str, int] = {}


@dataclass(frozen=True)
class ContinuationClaim:
    """Validated token claim plus durable idempotency state."""

    status: str
    jti: str
    answer: dict[str, Any] | None = None


@dataclass(frozen=True)
class PendingContinuationPayload:
    """Decrypted pending card after a successful claim.

    ``pending`` is the stored choice list. ``execution_id`` is the mint-time
    id; complete and fail must use it, not a later reply ``run_id``.
    """

    jti: str
    execution_id: str
    question: str
    pending: dict[str, Any]
    record: dict[str, Any]


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: bytes) -> str:
    sig = hmac.new(
        settings.redaction_hmac_key.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).digest()
    return _b64url_encode(sig)


def make_continuation_token(
    principal: Principal,
    thread_id: str | None,
    exchange_id: str | None,
    question: str,
    *,
    now: datetime | None = None,
    ttl_seconds: int = _DEFAULT_TTL_S,
) -> str:
    """Return an HMAC-signed single-use token authorizing structured-only continuation."""
    ts = int((now or datetime.now(UTC)).timestamp())
    body = {
        "jti": uuid.uuid4().hex,
        "user_id": str(principal.user_id),
        "role": principal.role,
        "thread_id": thread_id,
        "exchange_id": exchange_id,
        "q_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question": question,
        "exp": ts + ttl_seconds,
    }
    if principal.entity_id is not None:
        body["entity_id"] = principal.entity_id
    if principal.manifest_hash is not None:
        body["manifest_hash"] = principal.manifest_hash

    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64url_encode(payload)}.{_sign(payload)}"


def verify_and_consume_continuation_token(
    token: str,
    *,
    principal: Principal,
    thread_id: str | None = None,
    question: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Verify token signature, expiry, principal, conversation, and atomically consume it."""
    body = _validated_body(
        token, principal=principal, thread_id=thread_id, question=question, now=now
    )
    if body is None:
        return False
    jti = body["jti"]
    ts = int((now or datetime.now(UTC)).timestamp())
    exp = int(body["exp"])

    # Atomic single-use consumption check
    with _lock:
        # Prune expired tokens periodically
        expired_keys = [k for k, v in _CONSUMED_JTIS.items() if ts > v]
        for k in expired_keys:
            _CONSUMED_JTIS.pop(k, None)

        if jti in _CONSUMED_JTIS:
            return False
        _CONSUMED_JTIS[jti] = exp

    return True


def _validated_body(
    token: str,
    *,
    principal: Principal,
    thread_id: str | None,
    question: str | None,
    now: datetime | None,
) -> dict[str, Any] | None:
    try:
        payload_b64, sig = token.rsplit(".", 1)
        payload = _b64url_decode(payload_b64)
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        body = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    jti = body.get("jti")
    if not jti or not isinstance(jti, str):
        return None
    if str(body.get("user_id")) != str(principal.user_id):
        return None
    if body.get("role") != principal.role or body.get("entity_id") != principal.entity_id:
        return None
    token_thread_id = body.get("thread_id")
    if token_thread_id is not None and token_thread_id != thread_id:
        return None
    if question is not None:
        expected_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()
        if body.get("q_hash") != expected_hash and body.get("question") != question:
            return None
    ts = int((now or datetime.now(UTC)).timestamp())
    try:
        exp = int(body.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if ts > exp:
        return None
    return body


async def claim_continuation_token(
    token: str,
    *,
    principal: Principal,
    thread_id: str | None = None,
    question: str | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ContinuationClaim | None:
    """Validate then atomically claim a continuation in the durable transcript store.

    Tests and local single-process mode retain the process-local fallback used
    by the original token helper; production conversation mode uses the same
    existing Redis store as transcript state.
    """
    body = _validated_body(
        token, principal=principal, thread_id=thread_id, question=question, now=now
    )
    if body is None:
        return None
    request_id = request_id or "implicit"
    if not settings.conversation_enabled:
        if not verify_and_consume_continuation_token(
            token,
            principal=principal,
            thread_id=thread_id,
            question=question,
            now=now,
        ):
            return None
        return ContinuationClaim("claimed", body["jti"])
    store = transcript_store.get_conversation_store()
    claim = await store.claim_continuation(body["jti"], request_id, expires_at=int(body["exp"]))
    return ContinuationClaim(claim.status, body["jti"], claim.answer)


async def complete_continuation_claim(jti: str, request_id: str, answer: dict[str, Any]) -> None:
    if not settings.conversation_enabled:
        return
    await transcript_store.get_conversation_store().complete_continuation(jti, request_id, answer)


async def complete_continuation_token(
    token: str,
    *,
    principal: Principal,
    thread_id: str | None,
    question: str,
    request_id: str,
    answer: dict[str, Any],
) -> None:
    """Persist the terminal answer for an already-claimed SSE continuation."""
    body = _validated_body(
        token, principal=principal, thread_id=thread_id, question=question, now=None
    )
    if body is None or not settings.conversation_enabled:
        return
    await complete_continuation_claim(body["jti"], request_id, answer)


def decode_continuation_token_question(token: str) -> str | None:
    """Extract the original question from a validly formed token (for question restoration)."""
    try:
        payload_b64, _ = token.rsplit(".", 1)
        payload = _b64url_decode(payload_b64)
        body = json.loads(payload.decode("utf-8"))
        return body.get("question")
    except Exception:
        return None


# --- SQL Clarification Tokens and Continuation Seams ---


def _principal_binding_hash(principal: Principal) -> str:
    raw = f"{principal.user_id}:{principal.role}:{principal.entity_id}:{principal.manifest_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _get_keyring() -> EventEncryptionKeyring | None:
    raw = getattr(settings, "business_query_event_encryption_keys", None)
    if not raw or not raw.strip():
        return None
    try:
        return EventEncryptionKeyring.parse(raw)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise KeyringConfigurationError(
            "Invalid BUSINESS_QUERY_EVENT_ENCRYPTION_KEYS configuration"
        ) from exc


def _resolve_conversation_store(
    store: ConversationStore | None,
    resources: ProcessResources | None,
) -> ConversationStore:
    if store is not None:
        return store
    if resources is not None:
        return resources.conversation_store
    if settings.conversation_enabled:
        return transcript_store.get_conversation_store()
    return transcript_store.InMemoryConversationStore()


def mint_clarify_ticket(
    *,
    principal: Principal,
    ttl_seconds: int = _DEFAULT_TTL_S,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Mint a clarify ticket and return ``(ticket, jti)``.

    Self-verify must succeed; a ticket we just signed must never fail closed
    into a sentinel jti that desyncs the card from the store.
    """
    jti = uuid.uuid4().hex
    ticket = make_clarify_ticket(principal=principal, jti=jti, ttl_seconds=ttl_seconds, now=now)
    verified = verify_clarify_ticket(ticket, now=now)
    if verified is None:
        raise RuntimeError("clarify ticket self-verify failed")
    return ticket, verified["jti"]


def make_clarify_ticket(
    *,
    principal: Principal,
    jti: str | None = None,
    ttl_seconds: int = _DEFAULT_TTL_S,
    now: datetime | None = None,
) -> str:
    """Return an opaque HMAC-signed ticket containing only version, key_id, jti, exp."""
    ts = int((now or datetime.now(UTC)).timestamp())
    ticket_jti = jti or uuid.uuid4().hex
    body = {
        "version": "2",
        "key_id": "v1",
        "jti": ticket_jti,
        "exp": ts + ttl_seconds,
    }
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64url_encode(payload)}.{_sign(payload)}"


def verify_clarify_ticket(
    token: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Verify clarify ticket signature and expiry."""
    try:
        payload_b64, sig = token.rsplit(".", 1)
        payload = _b64url_decode(payload_b64)
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        body = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if body.get("version") != "2":
        return None
    jti = body.get("jti")
    if not jti or not isinstance(jti, str):
        return None
    ts = int((now or datetime.now(UTC)).timestamp())
    try:
        exp = int(body.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if ts > exp:
        return None
    return body


async def create_pending_continuation(
    *,
    store: ConversationStore | None = None,
    resources: ProcessResources | None = None,
    jti: str,
    execution_id: str,
    thread_id: str | None,
    principal: Principal,
    question: str,
    pending_data: dict[str, Any],
    expires_at: int,
) -> None:
    """Create pending continuation record in the transcript store."""
    store = _resolve_conversation_store(store, resources)

    principal_binding_hash = _principal_binding_hash(principal)
    intent_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()

    keyring = _get_keyring()
    if keyring is not None:
        plaintext = json.dumps(pending_data).encode("utf-8")
        aad = f"continuation:{jti}:{execution_id}".encode()
        key_ver, nonce, ct = encrypt_bytes(keyring, plaintext, aad)
        pending_blob = {
            "key_version": key_ver,
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ct).decode("ascii"),
        }
    else:
        pending_blob = pending_data

    await store.create_pending_continuation(
        jti=jti,
        execution_id=execution_id,
        thread_id=thread_id,
        principal_binding_hash=principal_binding_hash,
        intent_hash=intent_hash,
        pending_blob=pending_blob,
        expires_at=expires_at,
        question=question,
    )


async def claim_pending_continuation(
    *,
    store: ConversationStore | None = None,
    resources: ProcessResources | None = None,
    jti: str,
    execution_id: str,
    idempotency_key_hash: str,
    principal: Principal,
    thread_id: str | None,
    lease_seconds: int = CONTINUATION_LEASE_SECONDS,
) -> StoreContinuationClaim:
    """Claim pending continuation with bounded lease."""
    store = _resolve_conversation_store(store, resources)

    principal_binding_hash = _principal_binding_hash(principal)
    return await store.claim_pending_continuation(
        jti=jti,
        execution_id=execution_id,
        idempotency_key_hash=idempotency_key_hash,
        principal_binding_hash=principal_binding_hash,
        thread_id=thread_id,
        lease_seconds=lease_seconds,
    )


def _decrypt_pending_blob(jti: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return the stored pending mapping from ``pending_blob``.

    Encrypted envelopes use the same AAD as write. A missing, undecryptable,
    or non-choice-list blob fails closed.
    """
    blob = record.get("pending_blob")
    if not isinstance(blob, dict):
        raise NotFoundError("Pending continuation payload is missing")
    encrypted = {"key_version", "nonce", "ciphertext"} <= set(blob)
    if encrypted:
        keyring = _get_keyring()
        if keyring is None:
            raise ContinuationClaimRejectedError("Pending continuation is not readable")
        try:
            nonce = base64.b64decode(blob["nonce"], validate=True)
            ciphertext = base64.b64decode(blob["ciphertext"], validate=True)
            aad = f"continuation:{jti}:{record['execution_id']}".encode()
            plaintext = decrypt_bytes(keyring, str(blob["key_version"]), nonce, ciphertext, aad)
            pending = json.loads(plaintext.decode("utf-8"))
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            InvalidTag,
        ):
            raise ContinuationClaimRejectedError("Pending continuation is not readable") from None
    else:
        pending = blob
    if not isinstance(pending, dict) or not isinstance(pending.get("choices"), list):
        raise ContinuationClaimRejectedError("Pending continuation is not a choice list")
    return pending


async def load_pending_continuation_payload(
    *,
    store: ConversationStore | None = None,
    resources: ProcessResources | None = None,
    jti: str,
) -> PendingContinuationPayload:
    """Load and decrypt the pending card for a claimed continuation."""
    store = _resolve_conversation_store(store, resources)

    record = await store.get_continuation_record(jti)
    if record is None:
        raise NotFoundError("Pending continuation is missing or expired")
    pending = _decrypt_pending_blob(jti, record)
    execution_id = record.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise ContinuationClaimRejectedError("Pending continuation is not readable")
    question = record.get("question")
    return PendingContinuationPayload(
        jti=jti,
        execution_id=execution_id,
        question=question if isinstance(question, str) else "",
        pending=pending,
        record=record,
    )


async def complete_pending_continuation(
    *,
    store: ConversationStore | None = None,
    resources: ProcessResources | None = None,
    jti: str,
    execution_id: str,
    terminal_outcome: dict[str, Any],
    aqid: str | None = None,
    digest: str | None = None,
) -> None:
    """Complete pending continuation with terminal outcome."""
    store = _resolve_conversation_store(store, resources)

    # Every store persists this record as JSON. Refusing a payload that cannot
    # be encoded here keeps the in-memory store from accepting what Redis would
    # reject at write time.
    json.dumps(terminal_outcome)
    await store.complete_pending_continuation(
        jti=jti,
        execution_id=execution_id,
        terminal_outcome=terminal_outcome,
        aqid=aqid,
        digest=digest,
    )


async def fail_pending_continuation(
    *,
    store: ConversationStore | None = None,
    resources: ProcessResources | None = None,
    jti: str,
    execution_id: str,
    error_detail: str | None = None,
) -> None:
    """Fail pending continuation."""
    store = _resolve_conversation_store(store, resources)

    await store.fail_pending_continuation(
        jti=jti,
        execution_id=execution_id,
        error_detail=error_detail,
    )


async def join_continuation(
    continuation_ref: str,
    idempotency_key_hash: str,
    deadline: Deadline,
    *,
    store: ConversationStore | None = None,
    resources: ProcessResources | None = None,
) -> ContinuationJoinResult:
    """Public seam for same-key continuation joining / waiting."""
    ticket = verify_clarify_ticket(continuation_ref)
    if ticket is None:
        return ContinuationJoinResult(status="expired")

    jti = ticket["jti"]
    store = _resolve_conversation_store(store, resources)

    join_redis_client = resources.join_redis if resources is not None else None

    rec = await store.get_continuation_record(jti)
    if rec is None or time.time() > rec.get("expires_at", 0):
        return ContinuationJoinResult(status="expired")

    if rec.get("idempotency_key_hash") != idempotency_key_hash:
        return ContinuationJoinResult(status="mismatch", execution_id=rec.get("execution_id"))

    status = rec.get("status")
    if status == "COMPLETED":
        return ContinuationJoinResult(
            status="completed",
            execution_id=rec.get("execution_id"),
            answer=rec.get("terminal_outcome"),
            aqid=rec.get("terminal_aqid"),
            terminal_digest=rec.get("terminal_digest"),
        )
    if status == "TERMINAL_FAILED":
        return ContinuationJoinResult(status="failed", execution_id=rec.get("execution_id"))

    if status == "CLAIMED":
        if time.time() > (rec.get("lease_until") or 0):
            await store.fail_pending_continuation(
                jti, execution_id=rec["execution_id"], error_detail="lease_expired"
            )
            return ContinuationJoinResult(status="failed", execution_id=rec.get("execution_id"))

        wait_cap = min(float(deadline.remaining_seconds), 10.0)
        if wait_cap <= 0:
            return ContinuationJoinResult(
                status="continuation_unavailable", execution_id=rec.get("execution_id")
            )

        updated_rec = await store.wait_continuation_completion(
            jti,
            timeout_seconds=wait_cap,
            join_redis=join_redis_client,
        )
        if updated_rec is not None and updated_rec.get("status") == "COMPLETED":
            return ContinuationJoinResult(
                status="completed",
                execution_id=updated_rec.get("execution_id"),
                answer=updated_rec.get("terminal_outcome"),
                aqid=updated_rec.get("terminal_aqid"),
                terminal_digest=updated_rec.get("terminal_digest"),
            )
        return ContinuationJoinResult(
            status="continuation_unavailable", execution_id=rec.get("execution_id")
        )

    return ContinuationJoinResult(
        status="continuation_unavailable", execution_id=rec.get("execution_id")
    )
