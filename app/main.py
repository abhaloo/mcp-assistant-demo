"""
FastAPI application entry point.

MILESTONE 1: Get this running first.
Run with: uvicorn app.main:app --reload
Then open: http://localhost:8000/docs

PYTHON CONCEPTS YOU'LL HIT:
- Decorators (@app.get) — like TS decorators but used everywhere in Python
- Type hints in function signatures — FastAPI uses these to auto-generate docs
- async def — same concept as TS, different syntax
- f-strings — Python's template literals: f"hello {name}" vs `hello ${name}`
"""

import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.restore_evidence import router as restore_evidence_router
from app.api.router import router as ask_router
from app.business_query.definitions import (
    BundleValidationError,
    InvalidBundleIndexError,
    verify_bundles_startup,
)
from app.config import settings
from app.conversation.evidence.composition import build_evidence_restore_service
from app.health.background import start_background_checker, stop_background_checker
from app.health.router import router as health_router
from app.policy.manifest_loader import verify_manifest_startup
from app.providers.catalog_startup import verify_production_catalog_startup
from app.query_records.readiness import PostgresQueryRecordSchemaProbe
from app.resources import ProcessResources, bind_process_resources, reset_process_resources
from app.services.evidence_snapshots import snapshot_store_available
from app.telemetry import setup_telemetry, shutdown_telemetry
from app.telemetry.invocation_ledger import register_event_loop
from app.telemetry.sentry_setup import init_sentry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Lifespan: runs on startup and shutdown
# PYTHON CONCEPT: async context manager — the code before `yield` runs
# on startup, after `yield` runs on shutdown. Similar to Express middleware
# but scoped to the app lifecycle.
# ---------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup, clean up on shutdown."""
    logger.info("Starting up — Multi Color Printers Q&A API")
    resources = ProcessResources.from_settings()
    app.state.resources = resources
    # Restore needs the Query Record store and the evidence keyring; without
    # them the endpoint withholds every reference.
    app.state.evidence_restore_service = (
        build_evidence_restore_service(resources) if snapshot_store_available() else None
    )
    bind_token = bind_process_resources(resources)
    if settings.document_rag_enabled:
        logger.info("Document RAG enabled in this deployment")
    # Register this loop for the invocation ledger's cross-thread
    # scheduling before any request can dispatch a Business Query adapter
    # call to a ThreadPoolExecutor worker thread -- that thread has no
    # running loop of its own, so `asyncio.create_task` there would raise.
    # See app/telemetry/invocation_ledger.py::_schedule.
    register_event_loop()
    # Fail closed at startup (not lazily on the first record-tool call) if
    # app/policy/manifest/index.json is malformed or a listed bundle is
    # missing/tampered — a broken manifest set must never come up looking
    # healthy. See app/health/checks.py's ManifestIndexCheck for the ongoing
    # (post-startup) readiness half of this same guarantee.
    verify_manifest_startup()
    try:
        verify_bundles_startup()
    except (InvalidBundleIndexError, BundleValidationError, OSError, ValueError) as exc:
        # Degraded-capability startup, not a hard fail: the business-query
        # bundle (an independent, billing-side deploy) is the only thing
        # this guards, and it has zero production consumers today. A pure
        # policy-manifest version-skew hiccup on billing's side must not
        # brick document RAG and the existing SQL agent. ManifestIndexCheck
        # (app/health/checks.py) re-runs verify_bundles_startup() on every
        # readiness tick and reports the same failure there, so it is never
        # silently swallowed — it just stops being a startup crash. Runtime
        # stays fail-closed per request regardless: module.py maps a
        # bundle-load failure to Denied/Incomplete on every call, so this
        # is an availability trade-off, not a security regression.
        logger.error("business-query bundle validation failed at startup: %s", exc)
    verify_production_catalog_startup()
    init_sentry()
    health_task, health_stop = start_background_checker(
        query_record_schema_probe=(
            PostgresQueryRecordSchemaProbe() if settings.query_record_database_url.strip() else None
        )
    )
    yield
    # Cleanup runs here on shutdown. uvicorn triggers the lifespan shutdown on
    # SIGTERM/SIGINT, so this flushes buffered spans/metrics before exit —
    # the atexit hook the SDK registers doesn't reliably fire on container
    # SIGTERM, which is why we flush explicitly here.
    #
    # Every disposer below must run even if an earlier one raises, or one
    # broken teardown step leaks every engine and client behind it. Log and
    # move on to the next step instead of letting one failure strand the rest.
    teardown_steps: list[tuple[str, Callable[[], Awaitable[None] | None]]] = [
        ("stop_background_checker", lambda: stop_background_checker(health_task, health_stop)),
        ("shutdown_telemetry", shutdown_telemetry),
        ("app_resources_shutdown", app.state.resources.aclose),
    ]
    for name, step in teardown_steps:
        try:
            result = step()
            # Async steps return a coroutine, sync steps return None; the
            # difference is the dispatch, so no type inspection is needed.
            if result is not None:
                await result
        except Exception as exc:
            logger.warning("shutdown step %s failed: %s", name, exc)
    reset_process_resources(bind_token)
    logger.info("Shutting down")


# ---------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    lifespan=lifespan,
)

setup_telemetry(app)

# ---------------------------------------------------------------------
# CORS — allow your Next.js frontend to call this API
# ---------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------
# Routes
# Liveness/readiness live in app/health/router.py (/healthz, /readyz).
# ---------------------------------------------------------------------
app.include_router(health_router)
app.include_router(ask_router, prefix="/api")
app.include_router(restore_evidence_router, prefix="/api")
