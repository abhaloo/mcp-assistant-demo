"""
HMAC-JWT verifier — the FastAPI side of the service-to-service trust
boundary.

Laravel mints a short-lived HS256 JWT per request. RAG validates signature,
expiry, issuer, audience, one-time ``jti``, and optional operation ``scope``,
then builds a Principal. Access tiers come from the Principal — never the body.

Dual-parse: an optional ``record_access`` claim carries a v2 authorization
snapshot (see ``app/auth/record_access.py`` for the binding shape). v1
tokens (no ``record_access``, or an explicit JSON null) parse exactly as
before. A v2 claim is validated strictly and fails closed on any deviation
from the exact contract — it never silently degrades to v1 behavior.

Trusted record-context digest binding: an optional ``record_context_digest``
claim (``"sha256:" + hex``, same shape convention as
``record_access.manifest_hash``) binds the JWT to the ``record_context``
body field on ``/api/ask`` (see ``app/models/record_context.py`` for the
body shape and canonical-serialization recipe). Claim SHAPE parsing lives
here, in ``verify_principal()``, alongside every other claim — scope-
agnostic, so a malformed claim is always rejected regardless of which
endpoint the token is used against. The claim<->body BINDING check is a
separate dependency, ``enforce_record_context_binding`` below, wired only
onto the ask scope (only ``/api/ask`` requests can carry a
``record_context`` body) — see its docstring for why it is not folded into
``verify_principal``/``verify_principal_scope`` themselves.
"""

from __future__ import annotations

import re

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError

from app.auth.jti_replay import consume_jti
from app.auth.principal import Principal
from app.auth.record_access import RecordAccess
from app.config import settings
from app.models.record_context import compute_record_context_digest

_bearer = HTTPBearer(auto_error=False)

_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "user_id", "role", "jti"]
_ALLOWED_SCOPES = frozenset({"ask", "feedback", "cancel", "restore_evidence"})
_RECORD_CONTEXT_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-fA-F]{64}$")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _parse_tool_result_version(claims: dict) -> int | None:
    if "tool_result_version" not in claims:
        return None
    value = claims["tool_result_version"]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _unauthorized()
    return value


def _parse_department_id(claims: dict) -> int | None:
    if "department_id" not in claims:
        # PyJWT omits JSON null claims on decode; Laravel sends null explicitly.
        return None
    value = claims["department_id"]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _unauthorized()
    return value


def _parse_jti(claims: dict) -> str:
    value = claims.get("jti")
    if not isinstance(value, str) or not value or len(value) > 128:
        raise _unauthorized()
    return value


def _parse_scope(claims: dict) -> str | None:
    if "scope" not in claims:
        return None
    value = claims["scope"]
    if value is None:
        return None
    if not isinstance(value, str) or value not in _ALLOWED_SCOPES:
        raise _unauthorized()
    return value


def _parse_record_access(claims: dict) -> RecordAccess | None:
    """Detect and strictly parse the optional v2 ``record_access`` claim.

    Missing key or explicit JSON null => v1, returns None (same convention as
    ``_parse_department_id``/``_parse_scope``). Anything else must validate
    as a complete v2 ``RecordAccess`` — any deviation fails closed with the
    existing 401 contract; it never falls back to "treated as v1".
    """
    if "record_access" not in claims:
        return None
    value = claims["record_access"]
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _unauthorized()
    try:
        return RecordAccess.model_validate(value)
    except ValidationError:
        raise _unauthorized() from None


def _parse_record_context_digest(claims: dict) -> str | None:
    """Detect and strictly parse the optional v1 ``record_context_digest`` claim.

    Missing key or explicit JSON null => absent, returns None (same convention
    as ``_parse_department_id``/``_parse_scope``/``_parse_record_access``).
    Anything else must match ``"sha256:" + 64 hex chars`` or the whole request
    is rejected — this is claim SHAPE only; comparing it against the request
    body's ``record_context`` happens in ``enforce_record_context_binding``.
    """
    if "record_context_digest" not in claims:
        return None
    value = claims["record_context_digest"]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise _unauthorized()
    if not _RECORD_CONTEXT_DIGEST_PATTERN.fullmatch(value):
        raise _unauthorized()
    return value


async def verify_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    """Validate Bearer JWT and return Principal."""
    if creds is None:
        raise _unauthorized()

    try:
        claims = jwt.decode(
            creds.credentials,
            settings.rag_jwt_secret,
            algorithms=["HS256"],
            audience=settings.rag_jwt_aud,
            issuer=settings.rag_jwt_iss,
            leeway=10,
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.InvalidTokenError:
        raise _unauthorized() from None

    jti = _parse_jti(claims)
    if not await consume_jti(jti):
        raise _unauthorized()

    record_access = _parse_record_access(claims)
    record_context_digest = _parse_record_context_digest(claims)
    tool_result_version = _parse_tool_result_version(claims)

    return Principal(
        user_id=claims["user_id"],
        role=claims["role"],
        permissions=claims.get("permissions", []),
        department_id=_parse_department_id(claims),
        scope=_parse_scope(claims),
        jti=jti,
        entity_id=record_access.entity_id if record_access else None,
        cross_entity=record_access.cross_entity if record_access else None,
        manifest_hash=record_access.manifest_hash if record_access else None,
        scope_values=record_access.scope_values if record_access else None,
        resources=record_access.resources if record_access else None,
        document_tiers=record_access.document_tiers if record_access else None,
        record_context_digest=record_context_digest,
        tool_result_version=tool_result_version,
    )


def verify_principal_scope(expected_scope: str):
    """FastAPI dependency factory — requires a matching operation scope claim."""

    async def _dependency(
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> Principal:
        principal = await verify_principal(creds)
        if principal.scope != expected_scope:
            raise _unauthorized()
        return principal

    return _dependency


verify_principal_ask = verify_principal_scope("ask")
verify_principal_feedback = verify_principal_scope("feedback")
verify_principal_cancel = verify_principal_scope("cancel")
verify_principal_restore = verify_principal_scope("restore_evidence")


async def _raw_record_context(request: Request) -> object | None:
    """Best-effort read of the request body's raw ``record_context`` value.

    Any JSON-decode failure or non-object top-level body is treated as "no
    context" here — the existing request-validation 422 contract (from the
    route's own ``body: Question`` parsing, which runs AFTER this dependency)
    is what surfaces those cases; this function only ever contributes a 401,
    never a new way to fail on malformed JSON. Starlette caches
    ``Request.json()``/``Request.body()`` on the request instance, so reading
    it here does not consume the stream twice or diverge from what FastAPI's
    own body parsing sees later.
    """
    try:
        raw = await request.json()
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    return raw.get("record_context")


async def enforce_record_context_binding(
    request: Request,
    principal: Principal = Depends(verify_principal_ask),
) -> None:
    """The four-way ``record_context`` <-> ``record_context_digest`` binding
    check:

    - context present, claim missing  -> reject (401)
    - claim present, context missing  -> reject (401)
    - both present, digest mismatch   -> reject (401)
    - both absent                     -> normal request (v1, unchanged)

    A sibling FastAPI dependency to ``verify_principal_ask`` — not folded into
    ``verify_principal``/``verify_principal_scope``, which stay scope- and
    body-agnostic — so this only ever gates the ask route, the only place a
    ``record_context`` body field can exist. ``Depends(verify_principal_ask)``
    is the exact same callable object the ask route also depends on directly;
    FastAPI caches a dependency's result per request by callable identity, so
    the JWT is verified (and its one-time ``jti`` consumed) exactly once even
    though two places in the dependency tree ask for the Principal.

    This runs during FastAPI's dependency-resolution phase — before the route
    handler body (and its swallowing ``except Exception`` clause) starts, and
    before the route's own ``body: Question`` gets Pydantic-validated. A
    raised ``HTTPException`` here is never swallowed into a 500, and a
    binding failure is never silently treated as "context absent" (a
    shape-malformed-but-correctly-bound ``record_context`` still reaches the
    normal 422 body validation afterward; a mismatched/missing binding never
    gets that far).

    Computing the digest over the raw parsed body can itself raise on two
    inputs ``json.loads`` accepts but ``canonical_json_bytes`` cannot encode:
    a lone UTF-16 surrogate in a string value (valid JSON, valid Python
    ``str``, but ``.encode("utf-8")`` raises ``UnicodeEncodeError`` — surrogates
    have no UTF-8 representation), and ``NaN``/``Infinity`` numbers (Python's
    ``json.loads`` accepts these non-standard tokens by default, but this
    module's ``allow_nan=False`` makes re-encoding them raise ``ValueError``).
    Both are binding-integrity failures, not server bugs, so both are caught
    here and folded into the same static, no-echo ``_unauthorized()``
    response rather than surfacing as an unhandled 500.
    """
    raw_context = await _raw_record_context(request)
    claim_digest = principal.record_context_digest
    if raw_context is None and claim_digest is None:
        return
    if raw_context is None or claim_digest is None:
        raise _unauthorized()
    try:
        computed_digest = compute_record_context_digest(raw_context)
    except (UnicodeEncodeError, ValueError):
        raise _unauthorized() from None
    if computed_digest != claim_digest:
        raise _unauthorized()
