"""Verified caller identity -- the transport-free domain type.

``Principal`` is a pure pydantic model with no FastAPI dependency. It is
built by ``app/auth/jwt.py`` (the FastAPI-coupled verifier, ingress-layer)
from a validated JWT, but the type itself belongs to the business layer: it
is the ONLY source of truth for access control everywhere downstream of the
transport boundary. Moved out of ``app/auth/jwt.py`` so that business
modules that only need this type do not transitively import FastAPI (see
docs/superpowers/seam-consumer-map.md Seam 6 and
scripts/gates/check_dependency_direction.py rule 2-fastapi).
"""

from __future__ import annotations

from pydantic import BaseModel

from app.auth.record_access import ResourceGrant, ScopeValues


class Principal(BaseModel):
    """Verified caller identity -- the ONLY source of truth for access control."""

    user_id: str | int
    role: str
    permissions: list[str] = []
    department_id: int | None = None
    scope: str | None = None
    jti: str | None = None

    # v2 authorization-snapshot fields -- additive and optional. None on
    # every v1 token; populated only from a strictly validated v2
    # ``record_access`` claim. No existing field above changes meaning.
    entity_id: int | None = None
    cross_entity: bool | None = None
    manifest_hash: str | None = None
    scope_values: ScopeValues | None = None
    resources: dict[str, ResourceGrant] | None = None
    document_tiers: list[str] | None = None

    # Trusted record-context digest claim -- additive and optional. None
    # unless the JWT carries a strictly-shaped record_context_digest claim.
    # Binding against the request body's record_context field happens in
    # enforce_record_context_binding (app/auth/jwt.py), not here.
    record_context_digest: str | None = None

    # Versioned tool result claim -- additive and optional. None on legacy tokens;
    # positive int (1) when minted by caller supporting versioned tool results.
    tool_result_version: int | None = None
