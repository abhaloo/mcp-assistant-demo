"""Ask AI protocol version 2 service, Gate C enforcement, and streaming/JSON dispatcher."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.auth import Principal
from app.business_query.ports import BusinessProgressSink
from app.config import settings
from app.core.errors import (
    ContinuationClaimRejectedError,
    ContinuationRefRequiredError,
    ContinuationUnavailableError,
    DeadlineExpiredError,
    GateCBlockedError,
    NotFoundError,
    ResultPageDisabledError,
)
from app.models.ask_request import Question
from app.models.ask_response import Answer
from app.models.ask_v2_request import AskV2Request
from app.providers.stage_model_report import FIXED_RESPONSE_MODEL_SENTINEL
from app.resources import ProcessResources
from app.services.ask_deadline import Deadline
from app.services.ask_service import AskService
from app.services.ask_v2_gate_c import GateCStatus, check_gate_c_readiness
from app.services.continuation_tokens import (
    claim_pending_continuation,
    complete_pending_continuation,
    create_pending_continuation,
    fail_pending_continuation,
    join_continuation,
    load_pending_continuation_payload,
    mint_clarify_ticket,
    verify_clarify_ticket,
)
from app.telemetry.correlation import bind_thread_id, normalize_run_id

__all__ = ["AskService", "AskV2Service", "AskWireService", "GateCStatus", "check_gate_c_readiness"]


@dataclass(frozen=True)
class OperationOutcome:
    """One v2 operation's JSON result and its terminal-evidence expectation.

    Each ``_handle_*`` method decides ``expected_invocations`` at dispatch
    time, from the operation it is executing -- never by reading it back off
    the ``result`` it is about to return. A pure pagination or replay
    operation never commits to a provider call; an operation that dispatches
    a new turn does.
    """

    result: dict[str, Any]
    expected_invocations: int


def _choice_dicts(entries: Any) -> list[dict[str, Any]]:
    """Normalize id/label rows from a list of mappings or objects."""
    if not entries:
        return []
    choices: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            identifier, label = entry.get("id"), entry.get("label")
        else:
            identifier, label = getattr(entry, "id", None), getattr(entry, "label", None)
        if identifier is None:
            continue
        choices.append({"id": str(identifier), "label": str(label or identifier)})
    return choices


def _clarification_choices(disambiguation: Any) -> list[dict[str, Any]]:
    """Read selectable choices off a disambiguation payload of either shape.

    The payload carries ``candidates``; it arrives as a model on the typed path
    and as a mapping once a stored continuation has been round-tripped through
    JSON.
    """
    if disambiguation is None:
        return []
    candidates = (
        disambiguation.get("candidates")
        if isinstance(disambiguation, dict)
        else getattr(disambiguation, "candidates", None)
    )
    return _choice_dicts(candidates)


def _wire_clarification_choices(business_query: Any) -> list[dict[str, Any]]:
    """Prefer planner/stall ``choices``; fall back to disambiguation candidates."""
    from_outcome = _choice_dicts(getattr(business_query, "choices", None))
    if from_outcome:
        return from_outcome
    return _clarification_choices(getattr(business_query, "disambiguation", None))


def _resolve_pending_choice(
    pending: dict[str, Any], choice_id: str | None
) -> dict[str, Any] | None:
    """The stored choice a clicked id names, or None for free text."""
    if not choice_id:
        return None
    choices = pending.get("choices")
    if not isinstance(choices, list):
        return None
    for entry in choices:
        if isinstance(entry, dict) and entry.get("id") == choice_id:
            return entry
    return None


def _planner_question_from_reply(
    *,
    pending: dict[str, Any],
    choice_id: str | None,
    free_text: str | None,
) -> tuple[dict[str, Any] | None, str]:
    """Return (matched choice, planner question). Never use a raw choice id."""
    if choice_id:
        choice = _resolve_pending_choice(pending, choice_id)
        if choice is None:
            raise ContinuationClaimRejectedError("Unknown clarification choice")
        if choice.get("value_prompt"):
            return choice, ""
        rewrite = choice.get("rewrite") or choice.get("label")
        if not isinstance(rewrite, str) or not rewrite.strip():
            raise ContinuationClaimRejectedError("Clarification choice has no rewrite or label")
        return choice, rewrite
    if not isinstance(free_text, str) or not free_text.strip():
        raise ContinuationClaimRejectedError("Clarification reply is empty")
    return None, free_text


def _operation_result(answer: Answer) -> dict[str, Any]:
    """The wire dump plus the TurnResult the Answer keeps out of it.

    The stream projects the tool-result fields and the restore reference from
    that object, so every operation hands it over the same way.
    """
    return {**answer.model_dump(), "turn_result": answer.turn_result}


class AskV2Service:
    """Orchestrator for Ask AI v2 request operations and streaming lifecycle."""

    def __init__(self, ask_service: AskService | None = None) -> None:
        self._ask_service = ask_service or AskService()

    def validate_deadline(self, deadline_at_ms: int, server_now_ms: int | None = None) -> Deadline:
        from app.services.ask_deadline import validate_client_deadline

        return validate_client_deadline(deadline_at_ms, server_now_ms=server_now_ms)

    async def check_readiness(
        self,
        resources: ProcessResources,
        *,
        turn_budget: Deadline | None = None,
        expiry_event: asyncio.Event | None = None,
    ) -> GateCStatus:
        """Gate C readiness, for the route to check before either transport
        commits to a response -- the router already holds an AskV2Service
        instance for validate_deadline/ask/stream, so this reuses that same
        narrow seam instead of adding another app.services import to the
        ingress layer's import surface."""
        if turn_budget is None:
            return await check_gate_c_readiness(resources)
        from app.services.ask_v2_turn_bound import new_expiry_event, run_operation_with_budget

        signal = expiry_event if expiry_event is not None else new_expiry_event()
        return await run_operation_with_budget(
            lambda: check_gate_c_readiness(resources),
            turn_budget,
            signal,
        )

    async def _reserve_execution_if_configured(
        self,
        *,
        correlation_id: str,
        question: str,
    ) -> None:
        """Open the Query Record row before the turn runs.

        A reservation knows the request, not the route. Routing is decided
        downstream, so the terminal write is the first writer for it.
        """
        from app.query_records.wiring import reserve_query_record_execution_wire

        await reserve_query_record_execution_wire(
            correlation_id=correlation_id,
            project_id=settings.query_record_project_id or "mcp-default",
            environment=settings.environment,
            question=question,
        )

    async def ask(
        self,
        request: AskV2Request,
        principal: Principal,
        *,
        resources: ProcessResources,
        deadline: Deadline,
        progress: BusinessProgressSink | None = None,
        readiness_already_checked: bool = False,
        expiry_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        """Public budget wrapper around one v2 operation body."""
        from app.services.ask_v2_turn_bound import (
            is_timeout_cause,
            new_expiry_event,
            persist_timeout_history,
            run_operation_with_budget,
            schedule_timeout_query_record,
        )

        signal = expiry_event if expiry_event is not None else new_expiry_event()
        reservation_attempted = False
        correlation_id = self._normalize_run_id(request.run_id)

        async def _body() -> dict[str, Any]:
            nonlocal reservation_attempted
            return await self._ask_body(
                request,
                principal,
                resources=resources,
                deadline=deadline,
                progress=progress,
                readiness_already_checked=readiness_already_checked,
                expiry_event=signal,
                mark_reservation=lambda: _set_reservation(),
            )

        def _set_reservation() -> None:
            nonlocal reservation_attempted
            reservation_attempted = True

        try:
            return await run_operation_with_budget(_body, deadline, signal)
        except BaseException as exc:
            if reservation_attempted and is_timeout_cause(exc, signal):
                schedule_timeout_query_record(
                    question=request.question or " ",
                    principal=principal,
                    correlation_id=correlation_id,
                    request=request,
                )
                await persist_timeout_history(
                    question=request.question or " ",
                    principal=principal,
                    thread_id=request.thread_id,
                    run_id=correlation_id,
                )
            if isinstance(exc, asyncio.CancelledError) and signal.is_set():
                raise DeadlineExpiredError("Turn budget expired") from exc
            raise

    async def _ask_body(
        self,
        request: AskV2Request,
        principal: Principal,
        *,
        resources: ProcessResources,
        deadline: Deadline,
        progress: BusinessProgressSink | None,
        readiness_already_checked: bool,
        expiry_event: asyncio.Event,
        mark_reservation: Callable[[], None],
    ) -> dict[str, Any]:
        """Execute Ask AI v2 JSON operation with Gate C check and execution reservation."""
        # Both entry paths (JSON handler and the SSE producer task) pass here,
        # so the query record stamps the thread on every v2 terminal.
        bind_thread_id(request.thread_id)
        if not readiness_already_checked:
            gate_c = await check_gate_c_readiness(resources)
            if not gate_c.is_ready:
                raise GateCBlockedError(gate_c.missing_inputs)

        correlation_id = self._normalize_run_id(request.run_id)
        mark_reservation()
        if not readiness_already_checked:
            await self._reserve_execution_if_configured(
                correlation_id=correlation_id,
                question=request.question or "",
            )

        if request.operation == "new_question":
            outcome = await self._handle_new_question(
                request,
                principal,
                resources,
                deadline,
                correlation_id=correlation_id,
                progress=progress,
                expiry_event=expiry_event,
            )
        elif request.operation == "clarification_reply":
            outcome = await self._handle_clarification_reply(
                request,
                principal,
                resources,
                deadline,
                correlation_id=correlation_id,
                progress=progress,
                expiry_event=expiry_event,
            )
        elif request.operation == "regenerate":
            outcome = await self._handle_regenerate(
                request,
                principal,
                resources,
                deadline,
                correlation_id=correlation_id,
                progress=progress,
                expiry_event=expiry_event,
            )
        elif request.operation == "result_page":
            outcome = await self._handle_result_page(
                request,
                principal,
                resources,
                deadline,
                correlation_id=correlation_id,
                progress=progress,
                expiry_event=expiry_event,
            )
        else:
            raise ValueError(f"Unsupported operation: {request.operation}")

        from app.telemetry.invocation_payload import (
            ExecutionIdentity,
            InvocationExpectation,
            require_terminal_evidence,
        )

        receipt = await require_terminal_evidence(
            ExecutionIdentity(correlation_id=correlation_id),
            InvocationExpectation(min_invocations=outcome.expected_invocations),
            deadline=deadline,
        )
        res = outcome.result
        if "evidence_digest" not in res:
            res["evidence_digest"] = receipt.evidence_digest
        return res

    def _normalize_run_id(self, run_id: str) -> str:
        return normalize_run_id(run_id)

    async def _handle_new_question(
        self,
        request: AskV2Request,
        principal: Principal,
        resources: ProcessResources,
        deadline: Deadline,
        *,
        correlation_id: str,
        progress: BusinessProgressSink | None = None,
        expiry_event: asyncio.Event | None = None,
    ) -> OperationOutcome:
        q_body = Question(
            question=request.question or " ",
            thread_id=request.thread_id,
            page_context=request.page_context,
            record_context=request.record_context,
            operation="new_question",
            run_id=correlation_id,
            idempotency_key=request.idempotency_key,
            response_policy=request.response_policy or "allow_partial",
        )
        answer = await self._ask_service.ask(
            q_body,
            principal,
            resources=resources,
            progress=progress,
            turn_budget=deadline,
            expiry_event=expiry_event,
        )

        # new_question commits to dispatching a real turn -- the operation
        # itself decides this before any answer exists, independent of what
        # the dispatch produces.

        # Answer.business_query is the typed wire outcome, not a mapping. Read
        # its fields; the clarification prompt is the outcome's own question.
        # Options come from outcome.choices, then disambiguation candidates.
        business_query = answer.business_query
        if business_query is not None and business_query.outcome == "clarification_required":
            # The pending record stores the same option list the card shows, so a
            # clicked resolver candidate resolves exactly like a planner choice.
            choices = _wire_clarification_choices(business_query)
            pending = business_query.model_dump(mode="json")
            pending["choices"] = choices
            ticket, jti = mint_clarify_ticket(principal=principal, ttl_seconds=300)
            await create_pending_continuation(
                store=resources.conversation_store,
                resources=resources,
                jti=jti,
                execution_id=correlation_id,
                thread_id=request.thread_id,
                principal=principal,
                question=request.question or "",
                pending_data=pending,
                expires_at=int(time.time()) + 300,
            )
            return OperationOutcome(
                result={
                    "outcome": "clarification_required",
                    "question": answer.question,
                    "continuation_ref": ticket,
                    "continuation": business_query.continuation,
                    "prompt": business_query.question,
                    "choices": choices,
                    "allow_free_text": True,
                    "disambiguation": answer.disambiguation,
                },
                expected_invocations=1,
            )

        # The AskService marks a reply no model produced (greeting, stale
        # context, capability refusal) with the fixed-response sentinel; such a
        # turn has no invocation to prove.
        fixed = answer.model == FIXED_RESPONSE_MODEL_SENTINEL
        return OperationOutcome(
            result=_operation_result(answer), expected_invocations=0 if fixed else 1
        )

    async def _handle_clarification_reply(
        self,
        request: AskV2Request,
        principal: Principal,
        resources: ProcessResources,
        deadline: Deadline,
        *,
        correlation_id: str,
        progress: BusinessProgressSink | None = None,
        expiry_event: asyncio.Event | None = None,
    ) -> OperationOutcome:
        if request.continuation_ref is None:
            raise ContinuationRefRequiredError("clarification_reply requires continuation_ref")
        verified = verify_clarify_ticket(request.continuation_ref)
        if verified is None:
            raise NotFoundError("Invalid or expired continuation_ref")

        jti = verified["jti"]
        key_hash = hashlib.sha256(request.idempotency_key.encode("utf-8")).hexdigest()

        claim = await claim_pending_continuation(
            store=resources.conversation_store,
            resources=resources,
            jti=jti,
            execution_id=correlation_id,
            idempotency_key_hash=key_hash,
            principal=principal,
            thread_id=request.thread_id,
        )

        # A clarification round-trip -- replay, join, or a fresh claim's SQL
        # execution below -- never itself expects fresh terminal evidence at
        # this transport layer.
        if claim.status == "completed" and claim.answer is not None:
            return OperationOutcome(result=claim.answer, expected_invocations=0)
        elif claim.status == "in_progress":
            join_res = await join_continuation(
                request.continuation_ref,
                idempotency_key_hash=key_hash,
                deadline=deadline,
                resources=resources,
            )
            if join_res.status == "completed" and join_res.answer is not None:
                return OperationOutcome(result=join_res.answer, expected_invocations=0)
            raise ContinuationUnavailableError("continuation_unavailable")
        elif claim.status == "rejected":
            raise ContinuationClaimRejectedError("Continuation claim rejected")

        return await self._dispatch_claimed_clarification(
            request,
            principal,
            resources,
            jti=jti,
            correlation_id=correlation_id,
            progress=progress,
            turn_budget=deadline,
            expiry_event=expiry_event,
        )

    async def _dispatch_claimed_clarification(
        self,
        request: AskV2Request,
        principal: Principal,
        resources: ProcessResources,
        *,
        jti: str,
        correlation_id: str,
        progress: BusinessProgressSink | None,
        turn_budget: Deadline,
        expiry_event: asyncio.Event | None = None,
    ) -> OperationOutcome:
        payload = await load_pending_continuation_payload(
            store=resources.conversation_store, jti=jti
        )
        choice, clarify_val = _planner_question_from_reply(
            pending=payload.pending,
            choice_id=request.clarification_choice_id,
            free_text=request.clarification_free_text,
        )
        mint_execution_id = payload.execution_id
        if choice is not None and choice.get("value_prompt"):
            outcome = await self._mint_value_prompt_card(
                request,
                principal,
                resources,
                correlation_id=correlation_id,
                original_question=payload.question,
                continuation=payload.pending.get("continuation"),
                value_prompt=str(choice["value_prompt"]),
            )
            await complete_pending_continuation(
                store=resources.conversation_store,
                resources=resources,
                jti=jti,
                execution_id=mint_execution_id,
                terminal_outcome=outcome.result,
            )
            return outcome

        q_body = Question(
            question=clarify_val,
            thread_id=request.thread_id,
            operation="clarification_reply",
            continuation_token=request.continuation_ref,
            run_id=correlation_id,
            idempotency_key=request.idempotency_key,
            response_policy=request.response_policy or "allow_partial",
        )
        completed = False
        try:
            answer = await self._ask_service.ask(
                q_body,
                principal,
                resources=resources,
                progress=progress,
                turn_budget=turn_budget,
                expiry_event=expiry_event,
            )
            res_dict = _operation_result(answer)
            await complete_pending_continuation(
                store=resources.conversation_store,
                resources=resources,
                jti=jti,
                execution_id=mint_execution_id,
                terminal_outcome=answer.model_dump(mode="json"),
            )
            completed = True
            return OperationOutcome(result=res_dict, expected_invocations=0)
        except (asyncio.CancelledError, DeadlineExpiredError):
            if not completed:
                await fail_pending_continuation(
                    store=resources.conversation_store,
                    resources=resources,
                    jti=jti,
                    execution_id=mint_execution_id,
                    error_detail="cancelled",
                )
            raise
        except Exception:
            if not completed:
                await fail_pending_continuation(
                    store=resources.conversation_store,
                    resources=resources,
                    jti=jti,
                    execution_id=mint_execution_id,
                    error_detail="incomplete",
                )
            raise

    async def _mint_value_prompt_card(
        self,
        request: AskV2Request,
        principal: Principal,
        resources: ProcessResources,
        *,
        correlation_id: str,
        original_question: str,
        continuation: str | None,
        value_prompt: str,
    ) -> OperationOutcome:
        """Deterministic second card asking for the value a choice needs.

        No model call and no SQL dispatch: the person picked a narrowing that
        needs a value only they know, so the turn ends with a fresh ticket
        whose pending data carries the original question forward.
        """
        ticket, jti = mint_clarify_ticket(principal=principal, ttl_seconds=300)
        await create_pending_continuation(
            store=resources.conversation_store,
            resources=resources,
            jti=jti,
            execution_id=correlation_id,
            thread_id=request.thread_id,
            principal=principal,
            question=original_question,
            pending_data={
                "outcome": "clarification_required",
                "question": value_prompt,
                "continuation": continuation,
                "choices": [],
                "allow_free_text": True,
            },
            expires_at=int(time.time()) + 300,
        )
        return OperationOutcome(
            result={
                "outcome": "clarification_required",
                "question": original_question,
                "continuation_ref": ticket,
                "continuation": continuation,
                "prompt": value_prompt,
                "choices": [],
                "allow_free_text": True,
                "disambiguation": None,
            },
            expected_invocations=0,
        )

    async def _handle_regenerate(
        self,
        request: AskV2Request,
        principal: Principal,
        resources: ProcessResources,
        deadline: Deadline,
        *,
        correlation_id: str,
        progress: BusinessProgressSink | None = None,
        expiry_event: asyncio.Event | None = None,
    ) -> OperationOutcome:
        q_body = Question(
            question=request.question or "regenerate",
            thread_id=request.thread_id,
            operation="regenerate",
            target_exchange_id=request.continuation_ref or "ex-latest",
            continuation_token=request.continuation_ref,
            run_id=correlation_id,
            idempotency_key=request.idempotency_key,
            response_policy=request.response_policy or "allow_partial",
        )
        # regenerate commits to dispatching a real turn, same as new_question.
        answer = await self._ask_service.ask(
            q_body,
            principal,
            resources=resources,
            progress=progress,
            turn_budget=deadline,
            expiry_event=expiry_event,
        )
        return OperationOutcome(result=_operation_result(answer), expected_invocations=1)

    async def _handle_result_page(
        self,
        request: AskV2Request,
        principal: Principal,
        resources: ProcessResources,
        deadline: Deadline,
        *,
        correlation_id: str,
        progress: BusinessProgressSink | None = None,
        expiry_event: asyncio.Event | None = None,
    ) -> OperationOutcome:
        if not settings.ask_result_page_enabled:
            raise ResultPageDisabledError("Paging through results is not available in Ask AI.")
        q_body = Question(
            question=None,
            thread_id=request.thread_id,
            operation="result_page",
            result_page_cursor=request.result_page_cursor,
            run_id=correlation_id,
            idempotency_key=request.idempotency_key,
            response_policy=request.response_policy or "allow_partial",
        )
        answer = await self._ask_service.ask(
            q_body,
            principal,
            resources=resources,
            progress=progress,
            turn_budget=deadline,
            expiry_event=expiry_event,
        )
        return OperationOutcome(result=_operation_result(answer), expected_invocations=0)

    async def stream(
        self,
        request: AskV2Request,
        principal: Principal,
        *,
        resources: ProcessResources,
        deadline: Deadline,
        disconnected: Callable[[], Awaitable[bool]] | None = None,
        expiry_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream Ask AI v2 SSE events."""
        from app.services.ask_v2_stream import stream_ask_v2_events

        async def _never_disconnected() -> bool:
            return False

        effective_disconnected = disconnected or _never_disconnected
        async for frame in stream_ask_v2_events(
            request,
            principal,
            effective_disconnected,
            resources=resources,
            deadline=deadline,
            service=self,
            expiry_event=expiry_event,
        ):
            yield frame


# Canonical Ask service alias
AskWireService = AskV2Service
