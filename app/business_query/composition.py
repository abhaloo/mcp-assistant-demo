"""One builder for Business Query — prod, eval, and canary (ADR 0054)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine import Engine

from app.auth import Principal
from app.business_query.budget import INTERACTIVE_BUDGET_POLICY
from app.business_query.compile.adapter import InternalCompilerAdapter
from app.business_query.compile.business_time import business_today
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import PlanRefused
from app.business_query.plan import LlmPlanner
from app.business_query.plan.value_resolver import SqlValueResolver
from app.business_query.ports import PlanStore, QueryRecordWritePort
from app.business_query.wire.module import (
    BusinessQueryEvidencePorts,
    BusinessQueryModule,
)
from app.business_query.wire.trace import QueryTrace
from app.config import settings
from app.core.ask_errors import resolve_production_route
from app.providers.model_purpose import ModelPurpose
from app.providers.route_policy import ResolvedModelRoute
from app.telemetry.invocation_ledger import get_ledger_scope, record_sql_execution

if TYPE_CHECKING:
    from app.business_query.plan import CallRecorder
    from app.business_query.plan.attempts import PlannerAttemptContext
    from app.business_query.ports import PlannerAttemptSink

DEFAULT_STEP_TIMEOUT_SECONDS: float = 30.0


@dataclass(frozen=True)
class BusinessQueryEvidenceConfig:
    """Process configuration consumed by Business Query evidence composition."""

    query_record_database_url: str
    project_id: str
    api_version: str
    payload_mode: str
    encryption_keys: str
    retention_days: int


def business_query_evidence_config() -> BusinessQueryEvidenceConfig:
    """Read process settings at the composition root, not in business services."""
    return BusinessQueryEvidenceConfig(
        query_record_database_url=settings.query_record_database_url.strip(),
        project_id=settings.query_record_project_id,
        api_version=settings.api_version,
        payload_mode=settings.business_query_event_payload_mode,
        encryption_keys=settings.business_query_event_encryption_keys,
        retention_days=settings.business_query_event_retention_days,
    )


def _bundle_business_date(bundle: DefinitionBundle) -> date | None:
    """Return the live calendar anchor, tolerating lightweight eval doubles.

    Production bundles are validated strings. The eval harness deliberately
    uses small ``MagicMock`` bundle doubles, so assembly must not turn an
    unrelated test double into a timezone parsing error; those callers retain
    the existing explicit-date/failed-closed behavior.
    """
    timezone = getattr(bundle, "business_timezone", None)
    if not isinstance(timezone, str) or not timezone:
        return None
    try:
        return business_today(timezone)
    except PlanRefused:
        # Keep invalid bundle timezone handling at the module's existing
        # terminal precondition instead of failing assembly with a 500.
        return None


def module_step_timeout_seconds(route: ResolvedModelRoute) -> float:
    """Route-derived per-step wait_for ceiling; independent of the DB statement kill.

    Applied at each of six independent ``asyncio.wait_for`` sites in
    ``BusinessQueryModule`` (planner, presenter, value resolver, adapter
    execute, and two evidence appends) -- not once for the whole operation.
    A 30s value bounds each step to 30s, not the request to 30s. Interactive
    transports pass ``planner_step_ceiling_seconds`` to further cap the
    planner site alone; eval and canary builders keep the full route budget.
    """
    if route.request_timeout_s is None:
        return DEFAULT_STEP_TIMEOUT_SECONDS
    return float(route.request_timeout_s)


@dataclass(frozen=True)
class ModulePlugins:
    trace: QueryTrace | None = None
    call_recorder: CallRecorder | None = None
    attempt_sink: PlannerAttemptSink | None = None
    attempt_context: PlannerAttemptContext | None = None
    evidence_ports: BusinessQueryEvidencePorts | None = None
    plan_store: PlanStore | None = None
    pagination_secret: str | None = None
    mint_page_cursor: bool = True
    query_record_writer: QueryRecordWritePort | None = None


def _planner_from_plugins(plugins: ModulePlugins) -> LlmPlanner:
    return LlmPlanner(
        call_recorder=plugins.call_recorder,
        trace=plugins.trace,
        attempt_sink=plugins.attempt_sink,
        attempt_context=plugins.attempt_context,
    )


def build_module(
    *,
    principal: Principal,
    engine: Engine,
    executor: ThreadPoolExecutor,
    bundle: DefinitionBundle,
    database_identity: str | None,
    statement_timeout_seconds: float = settings.adapter_statement_timeout_seconds,
    planner_step_ceiling_seconds: float | None = None,
    plugins: ModulePlugins = ModulePlugins(),
) -> BusinessQueryModule:
    """Construct BusinessQueryModule with route budget and always-on resolver."""
    route = resolve_production_route(ModelPurpose.record_reasoning)
    module_step_timeout = module_step_timeout_seconds(route)
    adapter = InternalCompilerAdapter(
        principal,
        engine,
        bundle,
        statement_timeout_seconds=statement_timeout_seconds,
        trace=plugins.trace,
        database_identity=database_identity,
    )
    return BusinessQueryModule(
        planner=_planner_from_plugins(plugins),
        adapters=[adapter],
        bundle_resolver=lambda _h: bundle,
        step_timeout_seconds=module_step_timeout,
        planner_step_ceiling_seconds=planner_step_ceiling_seconds,
        budget_policy=INTERACTIVE_BUDGET_POLICY,
        trace=plugins.trace,
        evidence_ports=plugins.evidence_ports,
        plan_store=plugins.plan_store,
        pagination_secret=plugins.pagination_secret,
        mint_page_cursor=plugins.mint_page_cursor,
        value_resolver=SqlValueResolver(
            engine,
            statement_timeout_seconds=statement_timeout_seconds,
            sql_recorder=record_sql_execution,
            ledger_scope=get_ledger_scope(),
        ),
        executor=executor,
        default_business_date=_bundle_business_date(bundle),
        query_record_writer=plugins.query_record_writer,
    )


def build_query_record_writer(session: Any) -> QueryRecordWritePort:
    """Compose the State-backed synchronous terminal projection port."""
    from app.query_records.answered_writer import PostgresAnsweredQueryRecordWriter

    return PostgresAnsweredQueryRecordWriter(session)
