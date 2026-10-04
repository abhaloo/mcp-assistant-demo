"""Map run lifecycle outcomes to Query Record terminal snapshots (S1b)."""

from __future__ import annotations

from decimal import Decimal
from time import monotonic
from typing import TYPE_CHECKING, get_args

from app.auth import Principal

if TYPE_CHECKING:
    from app.query_records.turn_content import TurnContent
from app.conversation.coordinator.contracts import StopReason
from app.conversation.turn import TurnContext
from app.models.schemas import Question
from app.prompts.registry import registry
from app.query_records.content import subject_digest
from app.query_records.context import TerminalSnapshot
from app.query_records.dispatch import (
    reserve_query_record_execution,
    schedule_query_record_write,
)
from app.services.ask_prepare import prompt_pipeline_for
from app.services.run_lifecycle import RunOutcome

_OUTCOME_MAP = {
    "completed": "success",
    "stopped": "stopped",
    "error": "error",
}

# A business query that resolves without raising still completes the Ask attempt,
# so the lifecycle outcome alone would record every refusal as a success. The
# resolver's own disposition is the truthful terminal state whenever it ran.
_ANSWERED_DISPOSITION = "answered"

# Coordinator StopReason → the v2 outcome the person was told. finished is a
# completed draft and is not in this map. Existing query-record values only.
_COORDINATOR_STOP_OUTCOME: dict[str, str] = {
    reason: "incomplete" for reason in get_args(StopReason) if reason != "finished"
}
_COORDINATOR_STOP_OUTCOME["clarified"] = "clarification_required"
_COORDINATOR_STOP_OUTCOME["cancelled"] = "stopped"


def _terminal_outcome(
    run_outcome: RunOutcome,
    resolver_disposition: str | None,
    stable_error_code: str | None = None,
) -> str:
    lifecycle_outcome = _OUTCOME_MAP.get(run_outcome, "error")
    if lifecycle_outcome != "success":
        return lifecycle_outcome
    if resolver_disposition is not None and resolver_disposition != _ANSWERED_DISPOSITION:
        return resolver_disposition
    stop_outcome = _COORDINATOR_STOP_OUTCOME.get(stable_error_code or "")
    if stop_outcome is not None:
        return stop_outcome
    return "success"


def resolve_prompt_version(query_type: str | None) -> str | None:
    if query_type is None:
        return None
    return registry.version(prompt_pipeline_for(query_type))


def schedule_from_terminal(
    *,
    body: Question,
    principal: Principal,
    ctx: TurnContext | None,
    run_outcome: RunOutcome,
    query_type: str | None,
    started_at: float,
    prompt_version: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cached_tokens: int | None = None,
    estimated_usd: Decimal | None = None,
    cost_status: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    correlation_id: str | None = None,
    resolver_query_id: str | None = None,
    resolver_disposition: str | None = None,
    bq_trace_json: str | None = None,
    stable_error_code: str | None = None,
    timeout: bool | None = None,
    cancelled: bool | None = None,
    turn_content: TurnContent | None = None,
) -> None:
    if not correlation_id:
        return

    context_mode = "jobs" if body.page_context is not None else "none"
    records_only = ctx.records_only if ctx is not None else False
    question = body.question or (ctx.original_question if ctx is not None else None)
    if not question:
        question = "[business query result page]"
    snapshot = TerminalSnapshot(
        correlation_id=correlation_id,
        question=question,
        principal=principal,
        terminal_outcome=_terminal_outcome(run_outcome, resolver_disposition, stable_error_code),
        run_outcome=run_outcome,
        query_type=query_type,
        requested_route=query_type,
        effective_route=query_type,
        prompt_version=prompt_version,
        context_mode=context_mode,
        records_only=records_only,
        cancelled=run_outcome == "stopped" if cancelled is None else cancelled,
        started_at=started_at,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_tokens=cached_tokens,
        estimated_usd=estimated_usd,
        cost_status=cost_status,
        model=model,
        provider=provider if model is not None else None,
        resolver_query_id=resolver_query_id,
        resolver_disposition=resolver_disposition,
        bq_trace_json=bq_trace_json,
        stable_error_code=stable_error_code,
        timeout=timeout,
        turn_content=turn_content,
    )
    schedule_query_record_write(snapshot)


def monotonic_start() -> float:
    return monotonic()


async def reserve_query_record_execution_wire(  # noqa: PLR0913 - the row's owner stamps ride with its reservation
    *,
    correlation_id: str,
    project_id: str,
    environment: str,
    question: str,
    requested_route: str | None = None,
    thread_id: str | None = None,
    principal: Principal | None = None,
) -> None:
    """Reserve execution row in Query Records before execution begins.

    The stamps equal the terminal write's own, so the terminal completes the row
    instead of contradicting it.
    """
    await reserve_query_record_execution(
        correlation_id=correlation_id,
        project_id=project_id,
        environment=environment,
        question=question,
        requested_route=requested_route,
        thread_id=thread_id,
        subject_digest=subject_digest(str(principal.user_id)) if principal else None,
        entity_id=(
            str(principal.entity_id) if principal and principal.entity_id is not None else None
        ),
    )
