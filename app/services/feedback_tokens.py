"""HMAC-signed feedback tokens binding trace_id to the authenticated principal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

from app.auth import Principal
from app.config import settings

_DEFAULT_TTL_S = 7 * 24 * 3600


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


def make_feedback_token(
    trace_id: str,
    principal: Principal,
    *,
    now: datetime | None = None,
    ttl_seconds: int = _DEFAULT_TTL_S,
) -> str:
    """Return a short-lived token authorizing feedback for ``trace_id``.

    A v2 principal's ``entity_id`` is bound into the token so an entity
    switch between mint and use invalidates it (matching the
    thread-ownership posture in ``app/conversation/transcript_store.py``).
    The key is added only when ``entity_id`` is not None — a v1 principal's
    payload shape stays byte-identical (no new key).
    """
    ts = int((now or datetime.now(UTC)).timestamp())
    body = {
        "trace_id": trace_id,
        "user_id": str(principal.user_id),
        "role": principal.role,
        "exp": ts + ttl_seconds,
    }
    if principal.entity_id is not None:
        body["entity_id"] = principal.entity_id
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64url_encode(payload)}.{_sign(payload)}"


def verify_feedback_token(
    token: str,
    *,
    trace_id: str,
    principal: Principal,
    now: datetime | None = None,
) -> bool:
    """Verify token signature, expiry, trace_id, and principal binding."""
    try:
        payload_b64, sig = token.rsplit(".", 1)
        payload = _b64url_decode(payload_b64)
        if not hmac.compare_digest(_sign(payload), sig):
            return False
        body = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return False

    if body.get("trace_id") != trace_id:
        return False
    if str(body.get("user_id")) != str(principal.user_id):
        return False
    if body.get("role") != principal.role:
        return False
    # None-safe exact match, same pattern as transcript_store.py's
    # entity-ownership check. A missing key decodes to None (v1/claimless
    # token), so this is a no-op when both sides are v1 — any mismatch
    # (entity switch, or a claimless token used by a now-entity-asserting
    # principal, or vice versa) fails closed. Forbidden == missing: no
    # distinct error shape from any other invalid token.
    if body.get("entity_id") != principal.entity_id:
        return False

    ts = int((now or datetime.now(UTC)).timestamp())
    if ts > int(body.get("exp", 0)):
        return False
    return True
