"""Transport-free identity facade.

Exports only pure-pydantic domain identity types -- no FastAPI dependency,
directly or transitively. Verification functions (``verify_principal*``,
``enforce_record_context_binding``) stay in ``app.auth.jwt``, the
FastAPI-coupled ingress-layer verifier; import them from there directly.
"""

from app.auth.principal import Principal
from app.auth.record_access import RecordAccess, ResourceGrant, ScopeValues

__all__ = [
    "Principal",
    "RecordAccess",
    "ScopeValues",
    "ResourceGrant",
]
