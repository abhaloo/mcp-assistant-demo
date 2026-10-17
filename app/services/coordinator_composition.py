"""Composition seam for assembling conversational coordinator model and tools."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.auth import Principal
from app.business_query.plan.plan_patch import PlanPatch
from app.business_query.wire.request import BusinessQueryOwnerHint
from app.conversation.coordinator.contracts import (
    COORDINATOR_CLARIFY,
    BusinessQuestion,
    RecordBinding,
)
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

logger = logging.getLogger(__name__)


def resolve_patch_subject(
    continues: PlanPatch | None, subjects: Mapping[str, str]
) -> PlanPatch | None:
    """Replace the patch subject exchange id with the stored answer query id.

    The model names the exchange id it saw, sometimes with the brackets the
    prompt shows it in; the plan store is keyed by answer query id. A subject
    absent from the map clears the patch so the turn falls back to fresh
    planning instead of touching the store.
    """
    if continues is None:
        return None
    resolved = subjects.get(continues.subject.strip().removeprefix("[").removesuffix("]"))
    if resolved is None:
        logger.info("patch subject unknown exchange=%s", continues.subject)
        return None
    return continues.model_copy(update={"subject": resolved})


def build_coordinator_model(
    resources: ProcessResources | None = None,
) -> CoordinatorModel:
    """Assembles the production CoordinatorModel with prompt and tool schema binding."""
    from app.providers import get_chat_model

    chat_model = get_chat_model(
        purpose=ModelPurpose.coordinator,
        resources=resources,
        reasoning_summary=True,
    )
    return ProviderCoordinatorModel(
        chat_model,
        prompt=CoordinatorPrompt(),
    )


def _owner_hint_from_binding(binding: RecordBinding | None) -> BusinessQueryOwnerHint | None:
    if binding is None:
        return None
    return BusinessQueryOwnerHint(
        resource_type=binding.resource,
        record_id=binding.value,
        binding_member=binding.member,
        source="binding",
    )


def _effective_owner_hint(
    *,
    page_hint: BusinessQueryOwnerHint | None,
    binding: RecordBinding | None,
) -> BusinessQueryOwnerHint | None:
    if page_hint is not None:
        if binding is not None and (
            binding.resource != page_hint.resource_type
            or str(binding.value) != str(page_hint.record_id)
        ):
            logger.info(
                "binding dropped: page scope names another record resource=%s",
                page_hint.resource_type,
            )
        return page_hint
    return _owner_hint_from_binding(binding)


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

    def bq_operation(
        question: BusinessQuestion, continues: PlanPatch | None = None
    ) -> PreparedBqOperation:
        # The BQ module paints its own steps and tables through the same sink,
        # and keeps the page record as owner exactly as the structured route.
        bq_reply = coord_turn.turn.bq_reply
        # A reply to the coordinator's own card is the next question, not an
        # answer to a business-query clarification; the engine must not re-plan it.
        if bq_reply is not None and bq_reply.continuation == COORDINATOR_CLARIFY:
            bq_reply = None
        owner_hint = _effective_owner_hint(
            page_hint=coord_turn.turn.owner_hint,
            binding=question.binding,
        )
        return build_prepared_bq_operation(
            question=question.raw,
            reading=question.reading,
            patch=resolve_patch_subject(continues, coord_turn.turn.continuation_subjects),
            principal=principal,
            resources=owned,
            correlation_id=correlation_id,
            progress=progress,
            owner_hint=owner_hint,
            idempotency_key=idempotency_key,
            turn_budget=budget,
            history=coord_turn.turn.bq_history,
            continuation=bq_reply.continuation if bq_reply is not None else None,
            clarification_reply=bq_reply.reply if bq_reply is not None else None,
            clarification_prompt=bq_reply.prompt if bq_reply is not None else None,
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
