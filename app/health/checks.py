from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

import httpx
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select
from sqlalchemy.engine import Engine

from app.config import Settings, settings
from app.health.ports import QueryRecordSchemaProbe
from app.providers.azure_route_attestation import (
    AzureRouteAttestationCheck,
    attestation_settings_configured,
)
from app.telemetry.metrics import record_business_query_failure

_T = TypeVar("_T")


class HealthCheck(Protocol):
    name: str

    async def run(self) -> bool: ...


async def _http_ok(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> bool:
    """GET `url`, treating any non-5xx status as healthy.

    Transport errors (timeout, connection refused) propagate — the background
    refresher's gather converts them to a not-ready result and logs with the
    check name, so individual checks don't each re-implement try/except.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url, headers=headers, params=params)
    return response.status_code < 500


class AzureOpenAIConnectivityCheck:
    name = "azure_openai"

    async def run(self) -> bool:
        # Managed identity: no api_key. Acquire an Entra ID token for the
        # Cognitive Services audience and present it as a bearer. Token mint is a
        # sync SDK call, so off-load it from the event loop.
        if not settings.azure_endpoint:
            return False
        from app.providers.azure_credential import (
            COGNITIVE_SERVICES_SCOPE,
            get_token_provider,
        )

        token = await asyncio.to_thread(get_token_provider(COGNITIVE_SERVICES_SCOPE))
        url = f"{settings.azure_endpoint.rstrip('/')}/openai/deployments"
        return await _http_ok(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params={"api-version": settings.azure_api_version},
        )


class OpenAIConnectivityCheck:
    name = "openai"

    async def run(self) -> bool:
        if not settings.base_url:
            return False
        url = f"{settings.base_url.rstrip('/')}/models"
        headers = (
            {"Authorization": f"Bearer {settings.model_api_key}"}
            if settings.model_api_key
            else None
        )
        return await _http_ok(url, headers=headers)


class AzureSearchConnectivityCheck:
    name = "azure_search"

    async def run(self) -> bool:
        # Managed identity: no api_key. Probe with a no-op *query*
        # (top=0), not get_index — the app's identity holds Search Index Data
        # Reader, which permits querying documents but NOT reading the index
        # definition. The check must exercise what the app actually does.
        if not settings.azure_search_endpoint:
            return False

        def _ping() -> bool:
            from azure.search.documents import SearchClient

            from app.providers.azure_credential import get_azure_credential

            client = SearchClient(
                settings.azure_search_endpoint,
                settings.azure_search_index,
                get_azure_credential(),
            )
            # Consume the pager so the request actually executes; top=0 returns
            # no documents but a 200 proves connectivity + read access.
            list(client.search(search_text="*", top=0))
            return True

        return await asyncio.to_thread(_ping)


class ChromaConnectivityCheck:
    name = "chroma"

    async def run(self) -> bool:
        from app.core.breakers import BreakerState, retriever_breaker

        if retriever_breaker.state is BreakerState.OPEN:
            return False

        def _ping() -> bool:
            from app.rag.retrieval.chroma_retriever import ChromaVectorStore

            ChromaVectorStore().ping()
            return True

        return await asyncio.to_thread(_ping)


class PresidioCheck:
    name = "presidio"

    async def run(self) -> bool:
        def _ping() -> bool:
            from app.guardrails.analyzer import get_analyzer

            get_analyzer()
            return True

        return await asyncio.to_thread(_ping)


class ManifestIndexCheck:
    """The policy-manifest index/bundle set must be verifiable before the
    process accepts traffic — a malformed ``index.json``, a
    missing bundle file, or a tampered filename/content pair fails startup
    (``verify_manifest_startup()``, called from both this check and
    ``app.main``'s lifespan) rather than surfacing lazily on the first
    record-tool call. ``verify_manifest_startup()`` reads through
    ``load_manifest_index()``/``load_manifest()``, both ``lru_cache``d for
    the life of the process, so post-startup readiness ticks re-run this
    check but hit the cache and re-verify nothing new — a bundle file
    tampered with AFTER a successful startup will NOT flip readiness until
    the process restarts. Readiness here reflects the startup verification
    result, not a live re-check. Unconditional (not gated by any settings.*
    flag) — the manifest is a vendored, code-versioned artifact, always
    present regardless of which providers/retriever this deployment profile
    uses.

    Deploy-doc implication: because readiness only reflects the startup
    result, a deploy profile fronting ``/readyz`` (e.g. a rolling or
    blue-green cutover) must treat a failing boot — the process never
    reaching a ready state — as a blocked deploy; there is no post-startup
    signal that will catch a bad manifest later."""

    name = "policy_manifest_index"

    async def run(self) -> bool:
        def _verify() -> bool:
            from app.business_query.definitions import verify_bundles_startup
            from app.policy.manifest_loader import verify_manifest_startup

            # verify_manifest_startup() is still a hard fail in app.main's
            # lifespan. verify_bundles_startup() (the business-query
            # definition bundle, a separate billing-side artifact) is NOT —
            # a bad bundle there degrades to a readiness failure instead of
            # blocking boot for a feature with zero production consumers;
            # this check is where that failure surfaces post-startup.
            verify_manifest_startup()
            verify_bundles_startup()
            return True

        return await asyncio.to_thread(_verify)


# Bounds BOTH the async wait (asyncio.wait_for below -- the DB accepted a
# connection but a query hangs forever, e.g. a lock wait) and the DB-level
# statement/read timeout (connect_args below -- pymysql's
# read_timeout/write_timeout, this project's only MySQL driver; see
# app/eval/sql/agent/billing_engine.py/app/policy/record_executor.py for the same driver
# choice). connect_args={"connect_timeout": ...} alone only bounds the
# CONNECT phase, not query execution -- without this, a DB that accepts
# connections but hangs on the query blocks run_checks_once's gather (app/
# health/background.py) forever, freezing every OTHER check's readiness
# flag right alongside it.
_RECORD_DB_PROBE_TIMEOUT_SECONDS = 5.0


def _probe_engine(url: str) -> Engine:
    """Short-lived engine for a readiness probe. Never pooled -- a probe must not
    hold a connection open past its own timeout."""
    return create_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 10,
            "read_timeout": _RECORD_DB_PROBE_TIMEOUT_SECONDS,
            "write_timeout": _RECORD_DB_PROBE_TIMEOUT_SECONDS,
        },
    )


async def _bounded_probe(target: Callable[[], _T] | Callable[[], Awaitable[_T]]) -> _T:
    coro = (
        target() if asyncio.iscoroutinefunction(target) else asyncio.to_thread(target)  # type: ignore[arg-type]
    )
    return await asyncio.wait_for(coro, timeout=_RECORD_DB_PROBE_TIMEOUT_SECONDS)


class RecordProjectionConnectivityCheck:
    """Proves the record-projection database is reachable and that every
    column each ACCEPTED manifest resource declares as readable
    (``resource.readable_fields``) is actually queryable in the live view --
    registered ONLY when ``settings.mcp_record_database_url`` is configured
    (see ``build_check_registry`` below), mirroring every other optional
    provider check in this module. This check is SOFT (see
    ``SOFT_CHECK_NAMES`` below): its result is reported on ``/readyz`` but
    never flips the 503, since a downstream most replicas' document/
    semantic path never touches must not de-register the whole fleet.

    Deliberately does NOT filter by department, group, or apply any data
    predicate: a bounded, column-projecting ``SELECT ... LIMIT 1`` per
    declared resource, no ``WHERE``/``GROUP BY`` at all, proves existence/
    queryability regardless of row content -- a null department, zero rows,
    or a resource that doesn't support grouping must never read as unready.
    A ``select(1)`` form would only prove the VIEW existed, not that every
    declared COLUMN did -- a column rename/drop in an existing view would
    stay green here and only fail live, as ``tool_error``, on the first
    real query. Projecting ``readable_fields`` (SQLAlchemy raises at
    EXECUTION time, not Table-construction time, for a column the live
    view doesn't have) closes that gap. Probes EVERY hash in
    ``accepted_manifest_hashes()`` (current + previous, at most two) so an
    in-flight N-1 token's schema is verified too, not just current's. This
    is a SEPARATE concern from ``ManifestIndexCheck`` above: that check
    verifies the manifest ARTIFACT (index.json + bundle files) is
    well-formed and self-consistent; this one verifies the LIVE DATABASE
    actually matches it."""

    name = "record_projection"

    async def run(self) -> bool:
        def _ping() -> bool:
            from app.policy.manifest_loader import (
                accepted_manifest_hashes,
                build_metadata,
                load_manifest,
            )

            engine = _probe_engine(settings.mcp_record_database_url)
            try:
                with engine.connect() as conn:
                    for manifest_hash in accepted_manifest_hashes():
                        manifest = load_manifest(manifest_hash)
                        metadata = build_metadata(manifest)
                        for resource in manifest.resources.values():
                            table = metadata.tables[resource.projection_name]
                            columns = [table.c[field] for field in resource.readable_fields]
                            conn.execute(select(*columns).limit(1))
                return True
            finally:
                engine.dispose()

        return await _bounded_probe(_ping)


class BusinessQueryResolverCoverageCheck:
    """Every resolvable capability member in each accepted manifest's
    bundle must carry complete lookup metadata (view, column, scope) before
    shadow/enabled traffic is served. Pure bundle inspection — no live
    SELECTs. Probes EVERY hash in ``accepted_manifest_hashes()``."""

    name = "business_query_resolver_coverage"

    async def run(self) -> bool:
        url = settings.mcp_record_database_url
        if url is None or not url.strip():
            return False

        def _verify() -> bool:
            from app.business_query.definitions import bundle_for_manifest
            from app.business_query.plan.value_resolver import assert_resolver_coverage
            from app.policy.manifest_loader import accepted_manifest_hashes

            for manifest_hash in accepted_manifest_hashes():
                assert_resolver_coverage(bundle_for_manifest(manifest_hash))
            return True

        return await asyncio.to_thread(_verify)


class BusinessQueryViewCheck:
    """Proves every BQ semantic view declared in ``VIEW_COLUMNS`` exists on
    ``mcp_record_database_url`` with the expected column names — a missing
    ``customer_order_number`` (or any other drift) must fail readiness when
    ``business_query_mode`` is ``shadow`` or ``enabled``, not the first
    live planner SQL. Uses ``SELECT ... LIMIT 0`` per view (column names
    only — no row values logged). Registered when BQ mode is not
    ``disabled``; hard (not in ``SOFT_CHECK_NAMES``) for shadow/enabled.
    When the record DB URL is unset or blank, ``run()`` fails closed
    without opening a connection."""

    name = "business_query_views"

    async def run(self) -> bool:
        url = settings.mcp_record_database_url
        if url is None or not url.strip():
            return False

        def _ping() -> bool:
            from app.business_query.definitions import (
                DETAIL_VIEW_COLUMNS,
                VIEW_COLUMNS,
                bundle_for_manifest,
                detail_source_columns,
            )
            from app.policy.manifest_loader import accepted_manifest_hashes

            # Both the semantic B0 views and the fixed B1 detail projections
            # are Billing-owned read contracts.  This probe runs as the
            # configured read-only user, so SELECT LIMIT 0 verifies visibility
            # and column grants without fetching business rows.
            expected_views = {**VIEW_COLUMNS, **DETAIL_VIEW_COLUMNS}
            for manifest_hash in accepted_manifest_hashes():
                bundle = bundle_for_manifest(manifest_hash)
                for source in bundle.detail_sources:
                    expected_views[source.projection_view] = expected_views.get(
                        source.projection_view, frozenset()
                    ) | detail_source_columns(source)

            engine = _probe_engine(url)
            try:
                with engine.connect() as conn:
                    for view_name, expected_columns in expected_views.items():
                        table = Table(
                            view_name,
                            MetaData(),
                            *[Column(col, Integer) for col in sorted(expected_columns)],
                        )
                        ordered = sorted(expected_columns)
                        result = conn.execute(select(*[table.c[col] for col in ordered]).limit(0))
                        actual = frozenset(result.keys())
                        missing = expected_columns - actual
                        if missing:
                            raise RuntimeError(
                                f"view {view_name!r} missing columns: {sorted(missing)}"
                            )
                return True
            finally:
                engine.dispose()

        return await _bounded_probe(_ping)


class QueryRecordSchemaCheck:
    """Fail readiness unless Query Record schema and transactions are usable."""

    name = "query_record_schema"

    def __init__(self, probe: QueryRecordSchemaProbe | None = None) -> None:
        self._probe = probe

    async def run(self) -> bool:
        if self._probe is None:
            record_business_query_failure(kind="schema")
            return False

        try:
            healthy = await _bounded_probe(self._probe.check)
            if not healthy:
                record_business_query_failure(kind="schema")
            return healthy
        except Exception:
            record_business_query_failure(kind="schema")
            return False


# Check NAMES whose failure must NOT flip /readyz's 503 --
# health_cache.snapshot() (app/health/router.py) still reports a soft
# check's status in the payload (nothing here removes it from that dict),
# it simply never gates readiness. record_projection is soft because every
# registry entry defaults to a hard dep, and a shared downstream most replicas'
# document/semantic path never touches must not de-register the whole fleet at once --
# the SAME hazard app/health/router.py's own errors_500_window comment already
# names for a different signal.
SOFT_CHECK_NAMES = frozenset({"record_projection"})


def _probes_openai(s: Settings) -> bool:
    """One predicate, replacing two non-equivalent copies. The union of the old pair:
    the direct-OpenAI chat path, or Business Query (which routes through openai-*
    catalog targets) -- either way, never with offline embeddings."""
    if s.embedding_provider == "offline":
        return False
    if s.document_rag_enabled and s.chat_provider == "openai":
        return True
    return s.business_query_mode != "disabled"


_CHECK_REGISTRY: tuple[tuple[Callable[[Settings], bool], Callable[..., HealthCheck]], ...] = (
    (lambda s: True, ManifestIndexCheck),
    (
        lambda s: (
            s.document_rag_enabled
            and s.embedding_provider != "offline"
            and s.chat_provider == "azure"
        ),
        AzureOpenAIConnectivityCheck,
    ),
    (
        lambda s: (
            s.document_rag_enabled
            and s.embedding_provider != "offline"
            and s.chat_provider == "azure"
            and attestation_settings_configured()
        ),
        AzureRouteAttestationCheck,
    ),
    (_probes_openai, OpenAIConnectivityCheck),
    (
        lambda s: s.document_rag_enabled and s.retriever_kind == "azure_search",
        AzureSearchConnectivityCheck,
    ),
    (lambda s: s.document_rag_enabled and s.retriever_kind == "chroma", ChromaConnectivityCheck),
    (lambda s: s.redaction_enabled, PresidioCheck),
    (lambda s: s.mcp_record_database_url is not None, RecordProjectionConnectivityCheck),
    (lambda s: s.business_query_mode != "disabled", QueryRecordSchemaCheck),
    (lambda s: s.business_query_mode != "disabled", BusinessQueryViewCheck),
    (
        lambda s: s.business_query_mode != "disabled" and s.mcp_record_database_url is not None,
        BusinessQueryResolverCoverageCheck,
    ),
)


def build_check_registry(
    *, query_record_schema_probe: QueryRecordSchemaProbe | None = None
) -> list[HealthCheck]:
    return [
        (
            QueryRecordSchemaCheck(query_record_schema_probe)
            if factory is QueryRecordSchemaCheck
            else factory()
        )
        for predicate, factory in _CHECK_REGISTRY
        if predicate(settings)
    ]
