"""Request-lifecycle observation for the Ask seam.

`app/services/ask_service.py` and `app/services/ask_stream.py` must not call
`record_request` or `schedule_from_terminal` directly, and must not manage
the root LangSmith tracing context inline (see
tests/architecture/test_ask_orchestrator_cross_cutting_boundary.py). This
module is their one seam for all three: the only place outside
`app.telemetry.metrics` / `app.query_records.wiring` that calls those two
functions, and the owner of the tracing-context gate extracted from
`AskService.ask` and `stream_ask_events`.

The orchestrators still decide WHEN and WITH WHAT ARGS to call these -- that
decision is Q&A business logic (completion status, effective route, cancel
checkpoints, terminal snapshot fields) and stays there. What moved here is
the act of reaching into telemetry and Query Records to act on it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import langsmith as ls

from app.auth import Principal
from app.config import settings
from app.conversation.turn import TurnContext
from app.models.schemas import Question
from app.query_records.wiring import schedule_from_terminal
from app.services.run_lifecycle import RunOutcome
from app.telemetry.langsmith_capture import get_capture_client
from app.telemetry.metrics import record_request

T = TypeVar("T")


async def with_root_tracing_context(fn: Callable[[], Awaitable[T]]) -> T:
    """Root tracing-context gate for one Ask attempt (JSON or SSE).

    With capture off, tracing is explicitly disabled instead of falling
    through to the process environment -- a bare `@traceable` would pick up
    a developer's local `LANGSMITH_TRACING` env var even with this app's own
    capture off. With capture on, the PII-scrubbing client and project bind
    for `fn`'s duration so the root run (and its inherited LangChain child
    runs) upload through it.
    """
    cc = get_capture_client()
    if cc is None:
        with ls.tracing_context(enabled=False):
            return await fn()
    with ls.tracing_context(enabled=True, client=cc, project_name=settings.langsmith_project):
        return await fn()


def note_request_outcome(*, query_type: str | None, outcome: str) -> None:
    """The request-outcome metric, called from the orchestrator's own
    outcome-resolution points -- each one is a distinct, mutually exclusive
    exit from one Ask attempt, so this fires at most once per attempt."""
    record_request(query_type=query_type, outcome=outcome)


def note_query_record_terminal(
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
    """Query Record terminal scheduling: the one call per Ask attempt that
    turns a resolved terminal outcome into a scheduled Query Record write."""
    schedule_from_terminal(
        body=body,
        principal=principal,
        ctx=ctx,
        run_outcome=run_outcome,
        query_type=query_type,
        started_at=started_at,
        prompt_version=prompt_version,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cost_status=cost_status,
        model=model,
        correlation_id=correlation_id,
        resolver_query_id=resolver_query_id,
        resolver_disposition=resolver_disposition,
        bq_trace_json=bq_trace_json,
        stable_error_code=stable_error_code,
        timeout=timeout,
        cancelled=cancelled,
    )
