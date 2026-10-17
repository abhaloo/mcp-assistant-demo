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

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import langsmith as ls

from app.auth import Principal
from app.config import settings
from app.conversation.turn import TurnContext
from app.models.schemas import Question
from app.query_records.wiring import schedule_from_terminal
from app.services.run_lifecycle import RunOutcome
from app.telemetry.langsmith_capture import get_capture_client
from app.telemetry.metrics import record_request

if TYPE_CHECKING:
    from app.query_records.turn_content import TurnContent

logger = logging.getLogger(__name__)

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


class TerminalWriter(Protocol):
    """Writes one query-record terminal per Ask attempt.

    ``record`` takes exactly ``note_query_record_terminal``'s keywords; the
    orchestrator's ``finally`` is its only caller."""

    def record(self, **kwargs: Any) -> None: ...


class ImmediateTerminalWriter:
    """Writes the terminal as soon as the attempt's ``finally`` resolves it."""

    def record(self, **kwargs: Any) -> None:
        note_query_record_terminal(**kwargs)


@dataclass(frozen=True)
class TerminalIdentity:
    body: Question
    principal: Principal
    started_at: float
    correlation_id: str


class DeferredTerminalWriter:
    """Captures the terminal so the last frame's writer can commit it later.

    The v2 stream stores the attempt's terminal ``finally`` arguments and
    commits them, with an error override when the stream itself fails,
    after its last frame. ``read_turn_content`` reads what the turn showed at
    commit time, so the terminal row keeps it (ADR 0085)."""

    def __init__(
        self,
        identity: TerminalIdentity | None = None,
        *,
        read_turn_content: Callable[[], TurnContent] | None = None,
    ) -> None:
        self._identity = identity
        self._read_turn_content = read_turn_content
        self._kwargs: dict[str, Any] | None = None
        self._committed = False

    def record(self, **kwargs: Any) -> None:
        if self._kwargs is not None:
            raise RuntimeError("terminal already recorded")
        self._kwargs = kwargs

    def _turn_content(self) -> TurnContent | None:
        """What the turn showed; None when there is no reader or it fails."""
        if self._read_turn_content is None:
            return None
        try:
            return self._read_turn_content()
        except Exception as exc:  # noqa: BLE001 - the content is optional; the terminal is not
            logger.warning("turn content not read: %s", type(exc).__name__)
            return None

    def commit(
        self,
        *,
        run_outcome: RunOutcome | None = None,
        stable_error_code: str | None = None,
        fallback_outcome: RunOutcome | None = None,
    ) -> None:
        """Commits the captured terminal once. ``run_outcome`` is an override: it
        replaces the recorded outcome when the stream, not the attempt, resolved
        the failure. ``fallback_outcome`` fills an empty writer only: the attempt's
        own recorded terminal always wins over it."""
        if self._committed:
            return
        self._committed = True
        turn_content = self._turn_content()
        if self._kwargs is None:
            if self._identity is not None:
                outcome: RunOutcome = run_outcome or fallback_outcome or "error"
                note_query_record_terminal(
                    body=self._identity.body,
                    principal=self._identity.principal,
                    ctx=None,
                    run_outcome=outcome,
                    query_type=None,
                    started_at=self._identity.started_at,
                    correlation_id=self._identity.correlation_id,
                    stable_error_code=stable_error_code,
                    cancelled=True if outcome == "stopped" else None,
                    turn_content=turn_content,
                )
                return
            logger.warning("terminal commit without a recorded terminal run_id=%s", None)
            return
        kwargs = dict(self._kwargs)
        if run_outcome is not None:
            kwargs["run_outcome"] = run_outcome
        if stable_error_code is not None:
            kwargs["stable_error_code"] = stable_error_code
        note_query_record_terminal(**kwargs, turn_content=turn_content)


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
        cached_tokens=cached_tokens,
        estimated_usd=estimated_usd,
        cost_status=cost_status,
        model=model,
        provider=provider,
        correlation_id=correlation_id,
        resolver_query_id=resolver_query_id,
        resolver_disposition=resolver_disposition,
        bq_trace_json=bq_trace_json,
        stable_error_code=stable_error_code,
        timeout=timeout,
        cancelled=cancelled,
        turn_content=turn_content,
    )
