"""The Ask input screen: one Jev call plus the field-spoof rule per message (ADR 0087).

Each call writes one priced ledger row. The decision rides a span, and any decision other
than a plain pass logs one line. No message text goes to spans or logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Span

from app.conversation.transcript_models import TranscriptTurn
from app.guardrails.input_screen.conversation import screen_conversation
from app.guardrails.input_screen.decision import (
    RULE_VERSION,
    ScreenDecision,
    ScreenMode,
    action_for,
    raised_flags,
)
from app.guardrails.input_screen.jev import (
    JEV_MODEL,
    JEV_PROVIDER,
    JevAnswer,
    JevClient,
    JevUnavailable,
    ask_jev,
)
from app.guardrails.input_screen.questions import QUESTION_DIGEST, input_state
from app.pricing.pricer import price_tokens
from app.telemetry.correlation import current_correlation_id
from app.telemetry.invocation_ledger import (
    ModelInvocationRecord,
    get_ledger_scope,
    ledger_store_configured,
    schedule_model_invocation,
)
from app.telemetry.invocation_payload import INPUT_SCREEN_PURPOSE, record_evidence_invocation
from app.telemetry.spans import input_screen_span

if TYPE_CHECKING:
    from app.resources import ProcessResources

logger = logging.getLogger(__name__)


class InputScreen:
    """Screens one message per call. Jev failures become an `unavailable` decision."""

    def __init__(self, *, mode: ScreenMode, client: JevClient) -> None:
        self._mode: ScreenMode = mode
        self._client = client

    async def check(
        self,
        *,
        message: str,
        person_role: str,
        history: Sequence[TranscriptTurn] = (),
    ) -> ScreenDecision:
        conversation = (
            await asyncio.to_thread(screen_conversation, list(history)) if history else []
        )
        state = input_state(person_role=person_role, message=message, conversation=conversation)
        started = time.perf_counter()
        answer: JevAnswer | None = None
        with input_screen_span() as span:
            try:
                answer = await ask_jev(self._client, state)
            except JevUnavailable as exc:
                decision = self._decide(message, None, started, unavailable_reason=exc.reason)
            else:
                decision = self._decide(message, answer, started)
            _annotate(span, self._mode, decision, len(conversation))
        _record(state, self._mode, decision, answer)
        _log(person_role, self._mode, decision)
        return decision

    def _decide(
        self,
        message: str,
        answer: JevAnswer | None,
        started: float,
        *,
        unavailable_reason: str | None = None,
    ) -> ScreenDecision:
        signals = answer.signals if answer is not None else None
        flags = raised_flags(signals, message)
        return ScreenDecision(
            action=action_for(self._mode, flags, answered=answer is not None),
            flags=flags,
            signals=signals,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            request_id=answer.request_id if answer is not None else None,
            unavailable_reason=unavailable_reason,
        )


def input_screen_for(resources: ProcessResources) -> InputScreen | None:
    """The screen for the configured mode; None when the mode is `off`."""
    mode = resources.input_screen_mode
    if mode == "off":
        return None
    return InputScreen(mode=mode, client=resources.get_typesafe_client())


def _annotate(
    span: Span, mode: ScreenMode, decision: ScreenDecision, conversation_messages: int
) -> None:
    span.set_attribute("input_screen.mode", mode)
    span.set_attribute("input_screen.action", decision.action)
    span.set_attribute("input_screen.flags", ",".join(decision.flags))
    span.set_attribute("input_screen.rule_version", RULE_VERSION)
    span.set_attribute("input_screen.latency_ms", decision.latency_ms)
    span.set_attribute("input_screen.conversation_messages", conversation_messages)
    if decision.unavailable_reason is not None:
        span.set_attribute("input_screen.unavailable_reason", decision.unavailable_reason)


def _record(
    state: dict[str, Any], mode: ScreenMode, decision: ScreenDecision, answer: JevAnswer | None
) -> None:
    input_tokens = answer.input_tokens if answer is not None else None
    output_tokens = answer.output_tokens if answer is not None else None
    priced = price_tokens(model=JEV_MODEL, input_tokens=input_tokens, output_tokens=output_tokens)
    correlation_id = current_correlation_id()
    record = ModelInvocationRecord(
        scope=get_ledger_scope(),
        correlation_id=correlation_id,
        purpose=INPUT_SCREEN_PURPOSE,
        route_key=None,
        provider=JEV_PROVIDER,
        model=JEV_MODEL,
        request_messages=json.dumps(
            {"state": state, "question_digest": QUESTION_DIGEST}, ensure_ascii=False
        ),
        response_content=json.dumps(
            {
                "answers": answer.answers if answer is not None else None,
                "mode": mode,
                "action": decision.action,
                "flags": list(decision.flags),
                "rule_version": RULE_VERSION,
            },
            ensure_ascii=False,
        ),
        reasoning_content=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=None,
        latency_ms=decision.latency_ms,
        estimated_usd=priced.estimated_usd,
        cost_status=priced.cost_status,
        error_class=decision.unavailable_reason,
    )
    schedule_model_invocation(record)
    if correlation_id and not ledger_store_configured():
        record_evidence_invocation(correlation_id, record)


def _log(person_role: str, mode: ScreenMode, decision: ScreenDecision) -> None:
    if decision.action == "pass":
        return
    fields = {
        "correlation_id": current_correlation_id(),
        "person_role": person_role,
        "screen_mode": mode,
        "screen_action": decision.action,
        "screen_flags": ",".join(decision.flags),
        "rule_version": RULE_VERSION,
        "unavailable_reason": decision.unavailable_reason,
    }
    # A rejected key would otherwise switch detection off without a sound.
    level = logging.ERROR if decision.unavailable_reason == "auth" else logging.INFO
    logger.log(level, "ask input screen decision", extra=fields)
