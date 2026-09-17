"""Composition seam for assembling conversational coordinator model and tools."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.auth import Principal
from app.conversation.coordinator.model import CoordinatorModel, ProviderCoordinatorModel
from app.conversation.coordinator.prompt import CoordinatorPrompt
from app.conversation.evidence.composition import build_evidence_restore_service
from app.core.turn_budget import UNBOUNDED_BUDGET, TurnBudget
from app.providers.model_purpose import ModelPurpose
from app.resources import ProcessResources, current_process_resources
from app.services.business_query_service import build_prepared_bq_operation
from app.services.coordinator_tools import (
    AskCoordinatorTools,
    CoordinatorProgress,
    CoordinatorTools,
)
from app.services.evidence_snapshots import snapshot_store_available
from app.services.tool_composition import build_document_handler, serving_document_executor

if TYPE_CHECKING:
    import pytest

    from app.services.ask_prepare import PreparedCoordinatorTurn
    from app.services.business_query_operation import PreparedBqOperation


def build_coordinator_model(
    resources: ProcessResources | None = None,
    progress: CoordinatorProgress | None = None,
) -> CoordinatorModel:
    """Assembles the production CoordinatorModel with prompt and tool schema binding."""
    from app.business_query.plan.thought_stream import ThoughtStreamObserver
    from app.providers import get_chat_model

    chat_model = get_chat_model(
        purpose=ModelPurpose.coordinator,
        resources=resources,
        reasoning_summary=True,
    )
    new_callbacks = (lambda: [ThoughtStreamObserver(progress)]) if progress is not None else list
    return ProviderCoordinatorModel(
        chat_model,
        prompt=CoordinatorPrompt(),
        new_callbacks=new_callbacks,
    )


def build_coordinator_tools(
    coord_turn: PreparedCoordinatorTurn,
    *,
    principal: Principal,
    budget: TurnBudget = UNBOUNDED_BUDGET,
    resources: ProcessResources | None = None,
    progress: CoordinatorProgress | None = None,
    lifecycle_run_id: str | None = None,
    idempotency_key: str | None = None,
) -> CoordinatorTools:
    """Assembles the production AskCoordinatorTools binding caller authorities."""
    owned = resources or current_process_resources()
    turn_ctx = coord_turn.turn.ctx
    # The BQ wire refuses an empty correlation id and a v1 body carries no
    # run id, so the turn always gets one: the lifecycle id when it exists.
    correlation_id = lifecycle_run_id or turn_ctx.run_id or uuid.uuid4().hex

    def bq_operation(question: str) -> PreparedBqOperation:
        # The BQ module paints its own steps and tables through the same sink,
        # and keeps the page record as owner exactly as the structured route.
        return build_prepared_bq_operation(
            question=question,
            principal=principal,
            resources=owned,
            correlation_id=correlation_id,
            progress=progress,
            owner_hint=coord_turn.turn.owner_hint,
            idempotency_key=idempotency_key,
            turn_budget=budget,
        )

    # The same document executor Ask serves on every other route.
    doc_handler = build_document_handler(serving_document_executor(owned))

    # Restoring an earlier answer needs the Query Record store and the evidence
    # keyring, the same condition the restore endpoint is served under.
    restore_service = build_evidence_restore_service(owned) if snapshot_store_available() else None

    return AskCoordinatorTools(
        principal=principal,
        budget=budget,
        correlation_id=correlation_id,
        record_context=getattr(turn_ctx, "record_context", None),
        bq_operation_factory=bq_operation,
        document_handler=doc_handler,
        candidates=coord_turn.context.candidates,
        thread_id=turn_ctx.thread_id or "",
        restore_service=restore_service,
        progress=progress,
    )


def install_coordinator(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: CoordinatorModel | None = None,
    tools: CoordinatorTools | None = None,
) -> None:
    """Patches coordinator_composition's model/tools factories for test harnesses."""
    if model is not None:
        monkeypatch.setattr(
            "app.services.coordinator_composition.build_coordinator_model",
            lambda *args, **kwargs: model,
        )
    if tools is not None:
        monkeypatch.setattr(
            "app.services.coordinator_composition.build_coordinator_tools",
            lambda *args, **kwargs: tools,
        )
