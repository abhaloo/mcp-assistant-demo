"""LlmPlanner — one structured-output call on the record_reasoning route."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from asyncio import CancelledError
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Literal

import openai
from langchain_core.messages import AIMessage, HumanMessage
from pydantic_core import ValidationError as PydanticValidationError

if TYPE_CHECKING:
    from app.business_query.outcomes import (
        ClarificationRequired,
        Denied,
        Incomplete,
        Unsupported,
    )
from app.business_query.plan.attempts import (
    PlannerAttemptContext,
    PlannerAttemptResponse,
    PlannerAttemptTerminal,
    PlannerRawShapeClass,
    PlannerTerminalClass,
    PlannerTerminalCode,
    PlannerValidationResult,
)
from app.business_query.plan.planned_set import PlannedQuerySet
from app.business_query.plan.planner_history_assembly import (
    ReasoningObserver as _ReasoningObserver,
)
from app.business_query.plan.planner_history_assembly import (
    assemble_planner_prompt,
    raw_shape_class,
    sanitized_response_digest,
    stamp_retry_budget,
)
from app.business_query.plan.planner_history_assembly import (
    structured_method as _structured_method,
)
from app.business_query.plan.planner_prompt import (
    _REPAIR_TEMPLATE,
    json_object_protocol_hash,
    planner_schema_hash,
)
from app.business_query.plan.planner_repair import (
    _SEMANTIC_REPAIR_MAX,
    _SEMANTIC_REPAIRABLE,
    DERIVED_SET_REPAIR_BONUS,
    PLANNER_REPAIR_MAX,
    PlannerRepairMixin,
    _PlannerProtocolFailure,
)
from app.business_query.plan.planner_response import (
    PLANNER_FAILURES,
    PlannerFailureCode,
    PlannerModelResponse,
    clarification_required_from_model,
    dialogue_history_digest,
    validate_model_payload,
)
from app.business_query.plan.planner_wire_schema import PLANNER_WIRE_SCHEMA
from app.business_query.plan.query_plan import BusinessQueryPlan, plan_fingerprint
from app.business_query.plan.shape_guard import plan_names_entity, wants_named_entity
from app.business_query.plan.thought_stream import ThoughtStreamObserver
from app.business_query.ports import BusinessProgressSink, PlannerAttemptSink
from app.business_query.wire.trace import QueryTrace
from app.telemetry.spans import planner_stage_span

logger = logging.getLogger(__name__)

CallRecorder = Callable[[str], None]
_UNSUPPORTED_MESSAGE = "that isn't available to ask here"


def _default_model_factory() -> Any:
    from app.providers.factory import get_chat_model
    from app.providers.model_purpose import ModelPurpose

    return get_chat_model(
        purpose=ModelPurpose.record_reasoning, temperature=0, reasoning_summary=True
    )


class LlmPlanner(PlannerRepairMixin):
    """ONE structured-output call on the record_reasoning route. Emits only the
    strict plan/clarify/unsupported union — never SQL, never Cube JSON."""

    def __init__(
        self,
        *,
        model_factory: Callable[[], Any] | None = None,
        call_recorder: CallRecorder | None = None,
        trace: QueryTrace | None = None,
        attempt_sink: PlannerAttemptSink | None = None,
        attempt_context: PlannerAttemptContext | None = None,
    ) -> None:
        if (attempt_sink is None) is not (attempt_context is None):
            raise ValueError("planner attempt sink and context must be supplied together")
        self._model_factory = model_factory or _default_model_factory
        self._call_recorder = call_recorder
        self._trace = trace
        self._attempt_sink = attempt_sink
        self._attempt_context = attempt_context
        self._attempt_consumed = False
        self._consumed_attempt_id: str | None = None
        self._repair_hint_code: str | None = None
        self._plan_dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None = None

    @property
    def consumed_attempt_id(self) -> str | None:
        """Durable attempt ID after start succeeds; case-repeat linkage reads this."""
        return self._consumed_attempt_id

    async def plan(
        self,
        question: str,
        card: str,
        *,
        business_date: date | None = None,
        retry_hint: str | None = None,
        trace: QueryTrace | None = None,
        clarification_exchange: tuple[str, str] | None = None,
        dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None = None,
        progress: BusinessProgressSink | None = None,
    ) -> BusinessQueryPlan | ClarificationRequired | Unsupported | Incomplete | Denied:
        """Plan one turn.

        ``dialogue`` is the prior-turn message list; it precedes the question in
        the prompt. ``clarification_exchange`` is the current turn's
        shown-question/reply pair; it follows the question. Both may be present.
        ``progress`` receives the provider's reasoning summary as thought
        deltas while the model plans; the thought is closed however the
        call ends.
        """
        with planner_stage_span() as span:
            try:
                result = await self._plan_impl(
                    question,
                    card,
                    business_date=business_date,
                    retry_hint=retry_hint,
                    trace=trace,
                    clarification_exchange=clarification_exchange,
                    dialogue=dialogue,
                    progress=progress,
                )
            except Exception as exc:
                span.record_exception(exc)
                span.set_attribute("bq.outcome", "error")
                raise
            finally:
                if progress is not None:
                    progress.finish_thought()
            span.set_attribute("bq.outcome", type(result).__name__)
            return result

    async def _plan_impl(
        self,
        question: str,
        card: str,
        *,
        business_date: date | None = None,
        retry_hint: str | None = None,
        trace: QueryTrace | None = None,
        clarification_exchange: tuple[str, str] | None = None,
        dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None = None,
        progress: BusinessProgressSink | None = None,
    ) -> BusinessQueryPlan | ClarificationRequired | Unsupported | Incomplete | Denied:
        from app.business_query.outcomes import (
            Incomplete,
            Unsupported,
        )

        started = time.perf_counter()
        writer = trace if trace is not None else self._trace
        self._plan_dialogue = dialogue
        attempt_id = await self._start_attempt()
        correlation_digest = hashlib.sha256(question.encode()).hexdigest()[:16]
        try:
            bound, prompt, config = self._build_bound_and_prompt(
                question,
                card,
                business_date=business_date,
                dialogue=dialogue,
                clarification_exchange=clarification_exchange,
                progress=progress,
                writer=writer,
            )
        except CancelledError:
            await self._finish_attempt(
                attempt_id,
                terminal_class=PlannerTerminalClass.CANCELLED,
                terminal_code=PlannerTerminalCode.PLANNER_CANCELLED,
                started=started,
            )
            raise
        except (TimeoutError, openai.APITimeoutError):
            self._mark_planner_latency(started, writer)
            return await self._failure_outcome(
                "planner_timeout",
                type_name="TimeoutError",
                correlation_digest=correlation_digest,
                attempt_id=attempt_id,
                started=started,
                writer=writer,
            )
        except json.JSONDecodeError:
            self._mark_planner_latency(started, writer)
            return await self._failure_outcome(
                "planner_invalid_json",
                type_name="JSONDecodeError",
                correlation_digest=correlation_digest,
                attempt_id=attempt_id,
                started=started,
                writer=writer,
            )
        except openai.BadRequestError as exc:
            self._mark_planner_latency(started, writer)
            return await self._failure_outcome(
                "planner_capability_mismatch",
                type_name=type(exc).__name__,
                correlation_digest=correlation_digest,
                attempt_id=attempt_id,
                started=started,
                writer=writer,
            )
        except _PlannerProtocolFailure as exc:
            self._mark_planner_latency(started, writer)
            if exc.response_shape is not None and exc.validation is not None:
                await self._commit_attempt_response(
                    attempt_id,
                    shape=exc.response_shape,
                    validation=exc.validation,
                )
            return await self._failure_outcome(
                exc.code,
                type_name=type(exc.__cause__ or exc).__name__,
                correlation_digest=correlation_digest,
                attempt_id=attempt_id,
                started=started,
                writer=writer,
            )
        except Exception as exc:
            self._mark_planner_latency(started, writer)
            return await self._failure_outcome(
                "planner_provider_unavailable",
                type_name=type(exc).__name__,
                correlation_digest=correlation_digest,
                attempt_id=attempt_id,
                started=started,
                writer=writer,
            )

        raw: object = None
        repair_error: str | None = None
        repair_human: str | None = None
        parsed: PlannerModelResponse | None = None
        raw_shape = PlannerRawShapeClass.OTHER
        round_started = started
        protocol_repairs = 0
        semantic_repairs = 0
        # +DERIVED_SET_REPAIR_BONUS: headroom for the one extra protocol repair a
        # derived-set plan may earn (planner_repair.py); the per-round repairable
        # check still gates whether an ordinary plan actually gets to use it.
        max_rounds = PLANNER_REPAIR_MAX + DERIVED_SET_REPAIR_BONUS + _SEMANTIC_REPAIR_MAX + 1
        for _repair_round in range(max_rounds):
            round_started = time.perf_counter()
            if self._call_recorder is not None:
                self._call_recorder("planner")
            messages = self._assemble_round_messages(
                prompt,
                raw,
                repair_human=repair_human,
                repair_error=repair_error,
            )
            protocol_exc: _PlannerProtocolFailure | None = None
            try:
                parsed, raw_shape, raw = await self._invoke_and_parse(
                    bound,
                    messages,
                    config=config,
                    started=started,
                    writer=writer,
                )
                if (
                    parsed.action == "plan"
                    and parsed.plan is not None
                    and wants_named_entity(question)
                    and not plan_names_entity(parsed.plan)
                    and semantic_repairs < _SEMANTIC_REPAIR_MAX
                    and PlannerTerminalCode.PLANNER_SHAPE_MISMATCH.value in _SEMANTIC_REPAIRABLE
                ):
                    attempt_id, repair_human = await self._apply_semantic_repair(
                        attempt_id,
                        shape=raw_shape,
                        round_started=round_started,
                        writer=writer,
                    )
                    semantic_repairs += 1
                    repair_error = None
                    continue
                break
            except CancelledError:
                if attempt_id is not None:
                    await self._finish_attempt(
                        attempt_id,
                        terminal_class=PlannerTerminalClass.CANCELLED,
                        terminal_code=PlannerTerminalCode.PLANNER_CANCELLED,
                        started=round_started,
                    )
                raise
            except (TimeoutError, openai.APITimeoutError):
                self._mark_planner_latency(started, writer)
                return await self._failure_outcome(
                    "planner_timeout",
                    type_name="TimeoutError",
                    correlation_digest=correlation_digest,
                    attempt_id=attempt_id,
                    started=round_started,
                    writer=writer,
                )
            except json.JSONDecodeError as exc:
                protocol_exc = _PlannerProtocolFailure(
                    "planner_invalid_json",
                    response_shape=PlannerRawShapeClass.JSON_STRING,
                    validation=PlannerValidationResult.INVALID_JSON,
                )
                protocol_exc.__cause__ = exc
            except openai.BadRequestError as exc:
                self._mark_planner_latency(started, writer)
                return await self._failure_outcome(
                    "planner_capability_mismatch",
                    type_name=type(exc).__name__,
                    correlation_digest=correlation_digest,
                    attempt_id=attempt_id,
                    started=round_started,
                    writer=writer,
                )
            except _PlannerProtocolFailure as exc:
                protocol_exc = exc
            except Exception as exc:
                self._mark_planner_latency(started, writer)
                return await self._failure_outcome(
                    "planner_provider_unavailable",
                    type_name=type(exc).__name__,
                    correlation_digest=correlation_digest,
                    attempt_id=attempt_id,
                    started=round_started,
                    writer=writer,
                )

            if protocol_exc is None:
                continue
            round_raw = protocol_exc.raw if protocol_exc.raw is not None else raw
            repaired = await self._apply_protocol_repair(
                attempt_id,
                protocol_exc,
                repair_round=protocol_repairs,
                round_started=round_started,
                started=started,
                writer=writer,
                correlation_digest=correlation_digest,
                raw=round_raw,
            )
            if isinstance(repaired, Incomplete):
                return repaired
            attempt_id, repair_error = repaired
            protocol_repairs += 1
            repair_human = None

        if parsed is None:
            return Incomplete(reason_code="adapter_invalid")
        if parsed.action == "clarify":
            if parsed.clarification_question is None:
                return Incomplete(reason_code="adapter_invalid")
            outcome = clarification_required_from_model(parsed, question)
            await self._commit_valid_attempt(
                attempt_id,
                validation=PlannerValidationResult.VALID_CLARIFICATION,
                terminal_code=PlannerTerminalCode.CLARIFICATION_REQUIRED,
                started=round_started,
                shape=raw_shape,
            )
            return outcome
        if parsed.action == "unsupported":
            if parsed.unsupported_reason is None:
                return Incomplete(reason_code="adapter_invalid")
            if writer is not None:
                writer.fail("planner", parsed.unsupported_reason)
            outcome = Unsupported(
                reason_code=parsed.unsupported_reason, message=_UNSUPPORTED_MESSAGE
            )
            await self._commit_valid_attempt(
                attempt_id,
                validation=PlannerValidationResult.VALID_UNSUPPORTED,
                terminal_code=PlannerTerminalCode.UNSUPPORTED,
                started=round_started,
                shape=raw_shape,
            )
            return outcome

        plan = parsed.plan
        if plan is None:
            return Incomplete(reason_code="adapter_invalid")
        await self._commit_valid_attempt(
            attempt_id,
            validation=PlannerValidationResult.VALID_PLAN,
            terminal_code=PlannerTerminalCode.PLAN_VALID,
            started=round_started,
            fingerprint=plan_fingerprint(plan),
            shape=raw_shape,
        )
        return PlannedQuerySet(
            primary=plan,
            companions=tuple(parsed.companion_plans or ()),
        )

    async def _start_attempt(self) -> str | None:
        if self._attempt_sink is None or self._attempt_context is None:
            return None
        if self._attempt_consumed:
            raise RuntimeError("planner attempt context already consumed")
        self._attempt_consumed = True
        start = self._attempt_context.model_copy(
            update={
                "schema_hash": planner_schema_hash(self._attempt_context.output_mode),
                "prompt_adjunct_hash": (
                    json_object_protocol_hash()
                    if self._attempt_context.output_mode == "json_object"
                    else None
                ),
                "history_digest": dialogue_history_digest(self._plan_dialogue),
            }
        )
        attempt_id = await self._attempt_sink.start(start)
        self._consumed_attempt_id = attempt_id
        return attempt_id

    async def _commit_valid_attempt(
        self,
        attempt_id: str | None,
        *,
        validation: PlannerValidationResult,
        terminal_code: PlannerTerminalCode,
        started: float,
        fingerprint: str | None = None,
        shape: PlannerRawShapeClass = PlannerRawShapeClass.JSON_OBJECT,
    ) -> None:
        if attempt_id is None:
            return
        await self._commit_attempt_response(
            attempt_id,
            shape=shape,
            validation=validation,
            fingerprint=fingerprint,
        )
        await self._finish_attempt(
            attempt_id,
            terminal_class=PlannerTerminalClass.COMPLETED,
            terminal_code=terminal_code,
            started=started,
        )

    async def _commit_attempt_response(
        self,
        attempt_id: str | None,
        *,
        shape: PlannerRawShapeClass,
        validation: PlannerValidationResult,
        fingerprint: str | None = None,
    ) -> None:
        if attempt_id is None:
            return
        assert self._attempt_sink is not None
        assert self._attempt_context is not None
        await self._attempt_sink.commit_response(
            attempt_id,
            expected_lease_epoch=self._attempt_context.lease_epoch,
            response=PlannerAttemptResponse(
                response_digest=sanitized_response_digest(shape, validation, fingerprint),
                raw_shape_class=shape,
                validation_result=validation,
                plan_fingerprint=fingerprint,
                repair_hint_code=self._repair_hint_code,
                committed_at=datetime.now(UTC),
            ),
        )

    async def _finish_attempt(
        self,
        attempt_id: str | None,
        *,
        terminal_class: PlannerTerminalClass,
        terminal_code: PlannerTerminalCode,
        started: float,
    ) -> None:
        if attempt_id is None:
            return
        assert self._attempt_sink is not None
        assert self._attempt_context is not None
        await self._attempt_sink.finish(
            attempt_id,
            expected_lease_epoch=self._attempt_context.lease_epoch,
            terminal=PlannerAttemptTerminal(
                terminal_class=terminal_class,
                terminal_code=terminal_code,
                duration_ms=round((time.perf_counter() - started) * 1000),
                finished_at=datetime.now(UTC),
            ),
        )

    def _build_bound_and_prompt(
        self,
        question: str,
        card: str,
        *,
        business_date: date | None,
        dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None,
        clarification_exchange: tuple[str, str] | None,
        progress: BusinessProgressSink | None,
        writer: QueryTrace | None,
    ) -> tuple[Any, list[Any], dict[str, Any] | None]:
        model = self._model_factory()
        stamp_retry_budget(model, writer)
        actual_output_mode = getattr(getattr(model, "spec", None), "structured_output_mode", None)
        if (
            self._attempt_context is not None
            and actual_output_mode != self._attempt_context.output_mode
        ):
            raise _PlannerProtocolFailure("planner_capability_mismatch")
        structured_method, add_json_object_protocol = _structured_method(model)
        if structured_method == "json_schema":
            wire_schema, structured_kwargs = PLANNER_WIRE_SCHEMA, {"strict": True}
        else:
            wire_schema, structured_kwargs = (
                PlannerModelResponse.model_json_schema(),
                {},
            )
        try:
            bound = model.with_structured_output(
                wire_schema, method=structured_method, **structured_kwargs
            )
        except Exception as exc:
            raise _PlannerProtocolFailure("planner_capability_mismatch") from exc
        callbacks = list(getattr(model, "callbacks", None) or [])
        if writer:
            callbacks.append(_ReasoningObserver(writer))
        if progress is not None:
            callbacks.append(ThoughtStreamObserver(progress))
        config = {"callbacks": callbacks} if callbacks else None
        prompt = assemble_planner_prompt(
            question,
            card,
            business_date=business_date,
            add_json_object_protocol=add_json_object_protocol,
            dialogue=dialogue,
            clarification_exchange=clarification_exchange,
        )
        return bound, prompt, config

    @staticmethod
    def _assemble_round_messages(
        prompt: list[Any],
        raw: object,
        *,
        repair_human: str | None,
        repair_error: str | None,
    ) -> list[Any]:
        if repair_human is None and repair_error is None:
            return prompt
        try:
            prior = json.dumps(raw) if isinstance(raw, dict) else str(raw)
        except TypeError:
            prior = str(raw)
        prior_content = prior[:8192]
        if repair_human is not None:
            return [
                *prompt,
                AIMessage(content=prior_content),
                HumanMessage(content=repair_human),
            ]
        return [
            *prompt,
            AIMessage(content=prior_content),
            HumanMessage(content=_REPAIR_TEMPLATE.replace("{errors}", repair_error or "")),
        ]

    async def _invoke_and_parse(
        self,
        bound: Any,
        messages: list[Any],
        *,
        config: dict[str, Any] | None,
        started: float,
        writer: QueryTrace | None,
    ) -> tuple[PlannerModelResponse, PlannerRawShapeClass, object]:
        raw = await bound.ainvoke(messages, config=config)
        self._mark_planner_latency(started, writer)
        if raw is None or raw == "":
            raise _PlannerProtocolFailure(
                "planner_empty_content",
                response_shape=PlannerRawShapeClass.EMPTY,
                validation=PlannerValidationResult.EMPTY_CONTENT,
                raw=raw,
            )
        raw_shape = raw_shape_class(raw)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise _PlannerProtocolFailure(
                    "planner_invalid_json",
                    response_shape=raw_shape,
                    validation=PlannerValidationResult.INVALID_JSON,
                    raw=raw,
                ) from exc
        if not isinstance(raw, dict):
            raise _PlannerProtocolFailure(
                "planner_schema_invalid",
                response_shape=raw_shape,
                validation=PlannerValidationResult.SCHEMA_INVALID,
                raw=raw,
            )
        try:
            parsed = validate_model_payload(raw)
        except (PydanticValidationError, TypeError, ValueError) as exc:
            raise _PlannerProtocolFailure(
                "planner_schema_invalid",
                response_shape=raw_shape,
                validation=PlannerValidationResult.SCHEMA_INVALID,
                raw=raw,
            ) from exc
        return parsed, raw_shape, raw

    def _mark_planner_latency(self, started: float, writer: QueryTrace | None) -> None:
        if writer is not None:
            writer.planner_ms = round((time.perf_counter() - started) * 1000, 1)

    def _note_failure(
        self, detail: str, writer: QueryTrace | None, *, members: list[str] | None = None
    ) -> None:
        if writer is not None:
            site = PLANNER_FAILURES[detail].check_site if detail in PLANNER_FAILURES else None
            writer.fail("planner", detail, members=members, grain_check_site=site)

    async def _failure_outcome(
        self,
        code: PlannerFailureCode,
        *,
        type_name: str,
        correlation_digest: str,
        attempt_id: str | None,
        started: float,
        writer: QueryTrace | None,
    ) -> Incomplete:
        failure = PLANNER_FAILURES[code]
        if attempt_id is not None:
            assert self._attempt_sink is not None
            assert self._attempt_context is not None
            if code == "planner_timeout":
                terminal_class = PlannerTerminalClass.TIMEOUT
            elif code == "planner_provider_unavailable":
                terminal_class = PlannerTerminalClass.PROVIDER_ERROR
            else:
                terminal_class = PlannerTerminalClass.PLANNER_PROTOCOL
            await self._finish_attempt(
                attempt_id,
                terminal_class=terminal_class,
                terminal_code=PlannerTerminalCode(code),
                started=started,
            )
        logger.warning(
            "planner failure class=%s code=%s site=%s correlation_digest=%s",
            type_name,
            failure.code,
            failure.check_site,
            correlation_digest,
        )
        self._note_failure(failure.code, writer)
        from app.business_query.outcomes import Incomplete

        return Incomplete(reason_code=failure.public_reason_code)
