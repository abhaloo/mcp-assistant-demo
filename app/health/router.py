from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app.config import EffectiveRouteSettings, effective_route_settings
from app.health.checks import SOFT_CHECK_NAMES, build_check_registry
from app.health.state import error_tracker, health_cache
from app.policy.compatibility import render_compatibility_document

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/capabilities")
async def capabilities():
    """Immutable release/compatibility metadata — CI/deployment
    verification only, never a per-request downgrade/authorization oracle.
    Returns a raw ``Response`` over the pre-serialized JSON text from
    ``render_compatibility_document()`` (rather than a dict for FastAPI's
    own encoder to serialize) so this endpoint's body is byte-identical to
    ``python -m app.policy.compatibility --json``'s stdout — one canonical
    serialization, not two that could drift apart."""
    return Response(content=render_compatibility_document(), media_type="application/json")


@router.get("/readyz")
async def readyz():
    # The registry is the single source of truth for what must be healthy:
    # every check it builds for the active profile is a hard dependency,
    # EXCEPT the names in SOFT_CHECK_NAMES -- a soft check's status still
    # appears in `checks` below (health_cache.snapshot() returns every
    # cached result, hard or soft), it just never gates `ready`.
    hard_deps = tuple(c.name for c in build_check_registry() if c.name not in SOFT_CHECK_NAMES)
    checks = health_cache.snapshot()
    ready = health_cache.all_ok(hard_deps)
    # errors_500_window is observability only — it does NOT gate readiness.
    # Gating readiness on a shared error count de-registers every replica at
    # once when a common downstream degrades (correlated cascading removal).
    body = {
        "ready": ready,
        "checks": checks,
        "errors_500_window": error_tracker.count(),
        EffectiveRouteSettings.BODY_KEY: effective_route_settings().model_dump(),
    }
    if ready:
        return body
    return JSONResponse(status_code=503, content=body)
