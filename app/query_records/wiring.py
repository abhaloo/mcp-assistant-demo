"""Map run lifecycle outcomes to Query Record terminal snapshots (S1b)."""

from __future__ import annotations

from time import monotonic

from app.auth import Principal
from app.conversation.turn import TurnContext
from app.models.schemas import Question
from app.prompts.registry import registry
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


def _terminal_outcome(run_outcome: RunOutcome, resolver_disposition: str | None) -> str:
    lifecycle_outcome = _OUTCOME_MAP.get(run_outcome, "error")
    if lifecycle_outcome != "success" or resolver_disposition is None:
        return lifecycle_outcome
    if resolver_disposition == _ANSWERED_DISPOSITION:
        return "success"
    return resolver_disposition


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
    cost_status: str | None = None,
    model: str | None = None,
    correlation_id: str | None = None,
    resolver_query_id: str | None = None,
    resolver_disposition: str | None = None,
    bq_trace_json: str | None = None,
    stable_error_code: str | None = None,
    timeout: bool | None = None,
    cancelled: bool | None = None,
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
        terminal_outcome=_terminal_outcome(run_outcome, resolver_disposition),
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
        cost_status=cost_status,
        model=model,
        resolver_query_id=resolver_query_id,
        resolver_disposition=resolver_disposition,
        bq_trace_json=bq_trace_json,
        stable_error_code=stable_error_code,
        timeout=timeout,
    )
    schedule_query_record_write(snapshot)


def monotonic_start() -> float:
    return monotonic()


async def reserve_query_record_execution_wire(
    *,
    correlation_id: str,
    project_id: str,
    environment: str,
    question: str,
    requested_route: str | None = None,
) -> None:
    """Reserve execution row in Query Records before execution begins."""
    await reserve_query_record_execution(
        correlation_id=correlation_id,
        project_id=project_id,
        environment=environment,
        question=question,
        requested_route=requested_route,
    )
