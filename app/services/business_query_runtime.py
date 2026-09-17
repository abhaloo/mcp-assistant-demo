"""Runtime adapters kept behind the Ask Business Query composition root.

These helpers are deliberately boring infrastructure: they translate the
service's correlation id and database session into the durable planner,
execution-event, and Query Record ports expected by the Business Query
module. Keeping that wiring in one small module leaves the public service
focused on request policy and outcome mapping.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Principal
from app.business_query.composition import (
    ModulePlugins,
    build_query_record_writer,
    business_query_evidence_config,
)
from app.business_query.loop import run_loop_turn
from app.business_query.outcomes import Incomplete
from app.business_query.plan.attempts import PlannerAttemptContext, PostgresPlannerAttemptSink
from app.business_query.seal.events import (
    EventPayloadMode,
    PostgresExecutionEventStore,
)
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.business_query.wire.module import (
    BusinessProgressSink,
    BusinessQueryEvidenceContext,
    BusinessQueryRequest,
)
from app.config import settings
from app.core.ask_errors import resolve_production_route
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.crypto.event_keyring import EventEncryptionKeyring
from app.providers.model_purpose import ModelPurpose
from app.services.business_query_mapping import map_outcome
from app.services.tool_composition import (
    bind_tool_catalog,
    build_production_bq_handler,
    empty_document_handler,
    is_tool_layer_enabled,
)
from app.services.tool_turn import reduce_tool_turn
from app.tools.turn_contracts import (
    BqTurnPort,
    ToolTurnContext,
    ToolTurnExecution,
    ToolTurnRequest,
)


def _normalize_question(question: str | None) -> str | None:
    if question is None:
        return None
    return " ".join(question.casefold().split()) or None


def scoped_evidence_idempotency_key(
    *,
    correlation_id: str,
    principal: Any | None = None,
    question: str | None = None,
    page_cursor: str | None = None,
    public_idempotency_key: str | None = None,
    response_policy: str = "allow_partial",
    owner_hint: Any | None = None,
) -> str:
    """Derive a collision-safe durable key for one Business Query operation.

    A caller key is a retry token, not a globally unique authority.  Binding
    it to the verified principal and normalized request semantics lets retries
    dedupe across new transport run ids while preventing another user, policy,
    question, or response policy from reusing the same AQID.  Only the digest
    is persisted in the evidence idempotency key; raw question text never is.
    """
    principal_payload = (
        principal.model_dump(mode="json") if hasattr(principal, "model_dump") else principal
    )
    request_payload = {
        "operation": "business_query_page" if page_cursor is not None else "business_query",
        "question": _normalize_question(question),
        "page_cursor_digest": (
            hashlib.sha256(page_cursor.encode("utf-8")).hexdigest()
            if page_cursor is not None
            else None
        ),
        "response_policy": response_policy,
        "owner_hint": (
            owner_hint.model_dump(mode="json") if hasattr(owner_hint, "model_dump") else owner_hint
        ),
    }
    # Without a public retry key, correlation remains the operation identity.
    # With one, omit run/correlation so a transport retry can safely replay.
    payload = {
        "api_version": settings.api_version,
        "principal": principal_payload,
        "request": request_payload,
        "public_idempotency_key": public_idempotency_key,
        "correlation_id": None if public_idempotency_key else correlation_id,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"bq:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def planner_plugins(
    correlation_id: str,
    *,
    attempt_session: AsyncSession | None,
) -> ModulePlugins:
    """Build planner-attempt telemetry only when durable evidence is available."""
    evidence_config = business_query_evidence_config()
    if (
        not evidence_config.query_record_database_url
        or attempt_session is None
        or not correlation_id
    ):
        return ModulePlugins()

    route = resolve_production_route(ModelPurpose.record_reasoning)
    now = datetime.now(UTC)
    run_epoch = settings.api_version
    repeat_index = 0
    planner_call_index = 0
    context = PlannerAttemptContext(
        attempt_id=f"pa-{correlation_id}-r{repeat_index}-c{planner_call_index}",
        idempotency_key=f"{run_epoch}:{correlation_id}:{repeat_index}:{planner_call_index}",
        project_id=evidence_config.project_id,
        run_epoch=run_epoch,
        case_id=correlation_id,
        repeat_index=repeat_index,
        planner_call_index=planner_call_index,
        lease_owner=run_epoch,
        lease_epoch=1,
        lease_expires_at=now + timedelta(minutes=5),
        started_at=now,
        provider=route.provider,
        deployment=route.deployment,
        output_mode=route.structured_output_mode,
        effort=route.reasoning_effort,
    )
    return ModulePlugins(
        attempt_sink=PostgresPlannerAttemptSink(attempt_session),
        attempt_context=context,
    )


def execution_event_store(attempt_session: AsyncSession) -> PostgresExecutionEventStore:
    """Build the durable encrypted State port for a production Ask turn."""
    evidence_config = business_query_evidence_config()
    if evidence_config.payload_mode != "encrypted":
        raise ValueError("Business Query evidence payload mode must be encrypted")
    if not evidence_config.encryption_keys:
        raise ValueError("Business Query evidence encryption keyring is unavailable")
    try:
        keyring = EventEncryptionKeyring.parse(evidence_config.encryption_keys)
    except ValueError as exc:
        raise ValueError("Business Query evidence encryption keyring is invalid") from exc
    return PostgresExecutionEventStore(attempt_session, keyring=keyring)


def evidence_context(
    correlation_id: str,
    *,
    principal: Any | None = None,
    question: str | None = None,
    page_cursor: str | None = None,
    idempotency_key: str | None = None,
    response_policy: str = "allow_partial",
    owner_hint: Any | None = None,
) -> BusinessQueryEvidenceContext | None:
    """Return Ask evidence facts only when encrypted event storage is configured."""
    evidence_config = business_query_evidence_config()
    if not evidence_config.query_record_database_url:
        return None
    route = resolve_production_route(ModelPurpose.record_reasoning)
    now = datetime.now(UTC)
    return BusinessQueryEvidenceContext(
        idempotency_key=scoped_evidence_idempotency_key(
            correlation_id=correlation_id,
            principal=principal,
            question=question,
            page_cursor=page_cursor,
            public_idempotency_key=idempotency_key,
            response_policy=response_policy,
            owner_hint=owner_hint,
        ),
        project_id=evidence_config.project_id,
        retention_at=now + timedelta(days=evidence_config.retention_days),
        payload_mode=EventPayloadMode.ENCRYPTED,
        payload_classification="answer_release",
        route=route.route_id,
        provider=route.provider,
        deployment=route.deployment,
        output_mode=route.structured_output_mode,
        effort=route.reasoning_effort,
    )


async def module_query(
    operation: BqTurnPort,
    request: BusinessQueryRequest,
    *,
    progress: BusinessProgressSink | None = None,
    turn_budget: TurnBudget = UNBOUNDED_BUDGET,
) -> AskBusinessQueryResult:
    """Run one BQ-only Ask turn through the canonical graph."""
    catalog = bind_tool_catalog(
        bq_handler=build_production_bq_handler(operation),
        document_handler=empty_document_handler,
    )
    context = ToolTurnContext(
        principal=request.principal,
        correlation_id=request.correlation_id,
        budget=turn_budget,
        catalog=catalog,
        bq=operation,
        progress=progress,
        record_context=None,
        origin="ask",
    )
    execution = await run_loop_turn(_BQ_ONLY_TURN, context=context)
    branch: CommittedBqResult | AskBusinessQueryResult
    if isinstance(execution.bq, CommittedBqResult | AskBusinessQueryResult):
        branch = execution.bq
    else:
        branch = map_outcome(
            Incomplete(reason_code="adapter_invalid"),
            shadow=False,
            evidence_sealed=False,
        )
    return _unwrap(with_turn_result(branch, principal=request.principal))


_BQ_ONLY_TURN = ToolTurnRequest(query_type="structured", bq_requested=True, document=None)


def _unwrap(result: CommittedBqResult | AskBusinessQueryResult) -> AskBusinessQueryResult:
    return result.result if isinstance(result, CommittedBqResult) else result


def with_turn_result(
    result: CommittedBqResult | AskBusinessQueryResult, *, principal: Principal
) -> CommittedBqResult | AskBusinessQueryResult:
    """Attach the reduced TurnResult of a business-query-only turn.

    Only when the tool layer is on for the caller; otherwise the wire shape is
    returned unchanged. The reducer reads the committed shape, so a sealed
    result counts as committed. Both the structured route and the coordinator
    hand their results through here: one reducer decides what a turn proved.
    """
    if not is_tool_layer_enabled(principal):
        return result
    inner = _unwrap(result)
    execution = ToolTurnExecution(bq=result, document=None, refusal=None)
    reduced = replace(inner, turn_result=reduce_tool_turn(_BQ_ONLY_TURN, execution))
    # ``replace`` rebuilds from init fields only; the retained receipts are
    # attached after construction and must follow the result.
    retained = getattr(inner, "retained_members", None)
    if retained:
        object.__setattr__(reduced, "retained_members", retained)
    if isinstance(result, CommittedBqResult):
        return CommittedBqResult(result=reduced, answer_queries=result.answer_queries)
    return reduced


def evidence_ports(
    plugins: ModulePlugins,
    *,
    attempt_session: AsyncSession,
) -> ModulePlugins:
    """Attach all durable evidence ports to an already-built plugin set."""
    from app.business_query.compile.pagination import PostgresPlanStore
    from app.business_query.wire.module import BusinessQueryEvidencePorts

    return replace(
        plugins,
        evidence_ports=BusinessQueryEvidencePorts(
            event_store=execution_event_store(attempt_session)
        ),
        plan_store=PostgresPlanStore(attempt_session),
        pagination_secret=settings.rag_jwt_secret,
        mint_page_cursor=settings.ask_result_page_enabled,
        query_record_writer=build_query_record_writer(attempt_session),
    )
