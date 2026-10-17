"""Public SSE transport helpers for Ask streaming (extracted from ask_stream)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opentelemetry.trace import StatusCode

from app.config import settings
from app.conversation.turn import TurnContext
from app.core.ask_errors import report_ask_failure
from app.core.errors import RegenerateConflictError
from app.models.schemas import Question, Source
from app.providers.model_purpose import ModelPurpose
from app.providers.stage_model_report import FIXED_RESPONSE_MODEL_SENTINEL, StageModelAccumulator
from app.services.answer_finalize import bq_digest_for_persist, persist_and_enrich
from app.services.ask_frames import Disconnected
from app.services.ask_prepare import reference_artifact_for_turn
from app.services.cancel_service import begin_finalize, is_cancelled, mark_done
from app.services.continuation_tokens import complete_continuation_token
from app.services.run_lifecycle import RunOutcome, StreamTerminal
from app.telemetry.invocation_payload import (
    ExecutionIdentity,
    InvocationExpectation,
    require_terminal_evidence,
)
from app.telemetry.metrics import record_request
from app.telemetry.spans import transport_stage_span

if TYPE_CHECKING:
    from app.auth import Principal
    from app.services.ask_outcome import TurnEvidence
    from app.services.business_query_service import AskBusinessQueryResult
    from app.services.record_intent import RecordAggregateContinuation

# The fixed conflict copy every SSE producer emits on a stale regenerate
# target -- one string, so changing the wording means changing it once.
REGENERATE_CONFLICT_EVENT: dict[str, str] = {
    "error": "regenerate_conflict",
    "detail": "The conversation changed — ask your question again.",
}


@dataclass
class SseOutcomeBox:
    """Carries the terminal `RunOutcome` out of `sse_producer_errors`'s
    `except` clauses -- an `async with` body can't `return` its result
    directly, so the caller reads `box.value` once the block exits."""

    value: RunOutcome | None = None


@asynccontextmanager
async def sse_producer_errors(
    terminal: StreamTerminal,
    *,
    query_type: str | None,
    role: str,
    stage: str = "generate",
    pass_through: tuple[type[BaseException], ...] = (),
) -> AsyncIterator[SseOutcomeBox]:
    """Own the terminal error event and the request metric for one SSE producer.

    Every SSE producer here fails the same three ways: a stale regenerate
    target gets the fixed conflict event, a cooperative cancel is cleared and
    silent (the client already gave up -- no error event), and everything
    else becomes `report_ask_failure`'s payload. `query_type=None` skips the
    `record_request` metric call, for callers whose failures happen before a
    query_type has been dispatched to a producer. `pass_through` re-raises
    the named exception types unhandled, for a caller that gives a subset of
    failures their own, different payload.
    """
    box = SseOutcomeBox()
    try:
        yield box
    except RegenerateConflictError:
        if query_type is not None:
            record_request(query_type=query_type, outcome="error")
        await terminal.emit("error", dict(REGENERATE_CONFLICT_EVENT))
        box.value = "error"
    except asyncio.CancelledError:
        clear_task_cancellation()
        if query_type is not None:
            record_request(query_type=query_type, outcome="stopped")
        box.value = "stopped"
    except pass_through:
        raise
    except Exception as exc:
        if query_type is not None:
            record_request(query_type=query_type, outcome="error")
        await terminal.emit("error", report_ask_failure(exc, stage=stage, role=role))
        box.value = "error"


async def client_gone(disconnected: Disconnected, run_id: str | None) -> bool:
    if await disconnected():
        return True
    if run_id and await is_cancelled(run_id):
        return True
    return False


def clear_task_cancellation() -> None:
    """Allow traced cleanup after cooperative cancellation was observed."""
    task = asyncio.current_task()
    if task is not None:
        while task.cancelling():
            task.uncancel()


def sse_done_payload(
    stage_accumulator: StageModelAccumulator | None,
    *,
    final_producer_purpose: ModelPurpose | None = None,
    fixed_response: bool = False,
    extra: dict | None = None,
) -> dict:
    """SSE done payload with final-producer model and additive stage_models."""
    payload: dict = dict(extra or {})
    if fixed_response:
        payload["model"] = FIXED_RESPONSE_MODEL_SENTINEL
    elif stage_accumulator is not None and final_producer_purpose is not None:
        payload["model"] = stage_accumulator.producer_model(purpose=final_producer_purpose)
    else:
        payload["model"] = settings.active_chat_model
    if stage_accumulator is not None:
        payload["stage_models"] = stage_accumulator.build_report().model_dump()
    return payload


async def commit_done(
    *,
    body: Question,
    principal: Principal,
    ctx: TurnContext,
    disconnected: Disconnected,
    terminal: StreamTerminal,
    answer: str,
    done_payload: dict,
    run_id: str,
    follow_up_suggestions: list[str] | None = None,
    aggregate_continuation: RecordAggregateContinuation | None = None,
    answer_query_type: str | None = None,
    bq: AskBusinessQueryResult | None = None,
    model_invoked: bool,
    evidence: TurnEvidence | None = None,
) -> RunOutcome:
    """Cross the cancel/commit boundary once, then emit exactly one done.

    ``model_invoked`` states whether the route that produced this turn dispatched
    a model call. The caller decides it from the route it took, so the terminal
    evidence requirement never depends on fields read back off the answer.
    """
    if await client_gone(disconnected, run_id):
        return "stopped"
    if not await terminal.claim_done():
        return "error"
    if not await begin_finalize(run_id):
        await terminal.release_done()
        return "stopped"

    if run_id:
        try:
            await require_terminal_evidence(
                ExecutionIdentity(correlation_id=run_id),
                InvocationExpectation(min_invocations=1 if model_invoked else 0),
            )
        except BaseException:
            await terminal.release_done()
            raise

    try:
        with transport_stage_span(correlation_id=run_id) as span:
            try:
                result = await persist_and_enrich(
                    ctx=ctx,
                    principal=principal,
                    question=body.question,
                    answer=answer,
                    follow_up_suggestions=follow_up_suggestions,
                    reference_artifact=reference_artifact_for_turn(
                        body,
                        ctx,
                        answer_query_type=answer_query_type,
                    ),
                    aggregate_continuation=aggregate_continuation,
                    bq_digest=bq_digest_for_persist(query_type=answer_query_type, bq=bq),
                    run_id=run_id,
                    evidence=evidence,
                )
            except BaseException as exc:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR)
                span.set_attribute("bq.outcome", "error")
                raise
    except BaseException:
        await terminal.release_done()
        raise
    done_payload["follow_up_suggestions"] = result.follow_up_suggestions
    if result.restore_ref is not None and "tool_result_version" in done_payload:
        done_payload["restore_ref"] = result.restore_ref
    if result.persisted.thread_id is not None:
        done_payload["thread_id"] = result.persisted.thread_id
    if result.persisted.exchange_id is not None:
        done_payload["exchange_id"] = result.persisted.exchange_id
        if result.trace_id is not None:
            done_payload["trace_id"] = result.trace_id
            done_payload["feedback_token"] = result.feedback_token

    if body.continuation_token is not None:
        await complete_continuation_token(
            body.continuation_token,
            principal=principal,
            thread_id=ctx.thread_id,
            question=body.question,
            request_id=body.idempotency_key or body.run_id or "implicit",
            answer={
                "question": body.question,
                "answer": answer,
                "sources": [],
                "model": done_payload.get("model", "structured-only-replay"),
                "query_type": done_payload.get("query_type", "structured"),
                "fulfillment_scope": done_payload.get("fulfillment_scope"),
                "omitted_capabilities": done_payload.get("omitted_capabilities", []),
                "banner": done_payload.get("banner"),
                "completion_status": done_payload.get("completion_status"),
                "sql_stop_reason": done_payload.get("sql_stop_reason"),
                **(
                    {
                        "tool_result_version": done_payload["tool_result_version"],
                        "completeness": done_payload["completeness"],
                        "omissions": done_payload["omissions"],
                        "components": done_payload["components"],
                        "restore_ref": done_payload["restore_ref"],
                    }
                    if "tool_result_version" in done_payload
                    else {}
                ),
            },
        )

    emitted = await terminal.emit("done", done_payload)
    if emitted:
        await mark_done(run_id)
        return "completed"
    return "error"


def chunk_answer_tokens(text: str, max_frames: int = 30) -> list[str]:
    """Word-group chunks that concatenate back to exactly `text` (≤ max_frames)."""
    if text == "":
        return []
    words = text.split(" ")
    per = max(1, -(-len(words) // max_frames))
    chunks: list[str] = []
    for i in range(0, len(words), per):
        group = " ".join(words[i : i + per])
        if i + per < len(words):
            group += " "
        chunks.append(group)
    return chunks


def sse_sources_from_answer_sources(sources: list[Source]) -> list[dict]:
    """Map Answer Source objects to the SSE sources wire shape."""
    from app.services.ask_outcome import RetrievedSource, to_sse_source

    payload: list[dict] = []
    for i, source in enumerate(sources):
        source_file = source.source_file or "unknown"
        title = os.path.basename(str(source_file)) or str(source_file)
        payload.append(
            to_sse_source(
                RetrievedSource(
                    id=source.id or f"{source_file}:{i}",
                    content=source.content or "",
                    source_file=str(source_file),
                    title=title,
                    access_tier="unknown",
                    marker=source.marker,
                )
            )
        )
    return payload
