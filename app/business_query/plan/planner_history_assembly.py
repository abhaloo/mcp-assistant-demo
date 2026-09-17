"""Prompt, dialogue, and response history assembly for LlmPlanner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.business_query.plan.attempts import (
    PlannerRawShapeClass,
    PlannerValidationResult,
)
from app.business_query.plan.planner_prompt import (
    _JSON_OBJECT_PROTOCOL_ADJUNCT,
    build_planner_prompt,
)
from app.business_query.plan.planner_repair import _PlannerProtocolFailure
from app.business_query.wire.trace import QueryTrace
from app.providers.reasoning import extract_reasoning_evidence


class ReasoningObserver(BaseCallbackHandler):
    """Pull token counts and provider reasoning off the raw response."""

    def __init__(self, trace: QueryTrace) -> None:
        self._trace = trace

    def on_llm_end(self, response: Any, **_: Any) -> None:
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        reasoning = None
        for generations in getattr(response, "generations", []) or []:
            for generation in generations:
                message = getattr(generation, "message", None)
                if message is None:
                    continue
                meta = getattr(message, "usage_metadata", None) or {}
                if meta:
                    prompt = meta.get("input_tokens")
                    completion = meta.get("output_tokens")
                    reasoning = (meta.get("output_token_details") or {}).get("reasoning")
                text = extract_reasoning_evidence(message, max_chars=None).get("reasoning_text")
                if isinstance(text, str) and text.strip():
                    self._trace.reasoning = text
        for field_name, value in (
            ("tokens_prompt", prompt),
            ("tokens_completion", completion),
            ("tokens_reasoning", reasoning),
        ):
            if value is not None:
                setattr(self._trace, field_name, (getattr(self._trace, field_name) or 0) + value)


def structured_method(model: Any) -> tuple[str, bool]:
    mode = getattr(getattr(model, "spec", None), "structured_output_mode", None)
    if mode == "json_schema":
        return "json_schema", False
    if mode == "json_object":
        return "json_mode", True
    raise _PlannerProtocolFailure("planner_capability_mismatch")


def assemble_planner_prompt(
    question: str,
    card: str,
    *,
    business_date: date | None = None,
    add_json_object_protocol: bool = False,
    dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None = None,
    clarification_exchange: tuple[str, str] | None = None,
) -> list[BaseMessage]:
    prompt: list[BaseMessage] = list(
        build_planner_prompt(question, card, business_date=business_date)
    )
    if add_json_object_protocol:
        prompt = [
            prompt[0],
            HumanMessage(content=f"{prompt[1].content}\n\n{_JSON_OBJECT_PROTOCOL_ADJUNCT}"),
        ]
    # Dialogue turns are PRIOR conversation: they insert between the
    # system message and the card+question message, so the question
    # being planned stays the last human message. Appending them after
    # the question makes the model re-plan the earlier exchange.
    if dialogue is not None:
        dialogue_messages: list[BaseMessage] = []
        for role, text in dialogue:
            if role == "ai":
                dialogue_messages.append(AIMessage(content=text))
            elif role == "human":
                dialogue_messages.append(HumanMessage(content=text))
            else:
                raise ValueError(f"unsupported dialogue role: {role!r}")
        prompt = [prompt[0], *dialogue_messages, *prompt[1:]]
    # The clarification exchange continues the CURRENT turn, so its
    # shown-question/reply pair appends after the card+question
    # message and the reply stays the final human message.
    if clarification_exchange is not None:
        shown_question, reply = clarification_exchange
        prompt = [
            *prompt,
            AIMessage(content=shown_question),
            HumanMessage(content=reply),
        ]
    return prompt


def sanitized_response_digest(
    shape: PlannerRawShapeClass,
    validation: PlannerValidationResult,
    fingerprint: str | None,
) -> str:
    payload = json.dumps(
        {
            "raw_shape_class": shape,
            "validation_result": validation,
            "plan_fingerprint": fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def raw_shape_class(raw: object) -> PlannerRawShapeClass:
    if isinstance(raw, dict):
        return PlannerRawShapeClass.JSON_OBJECT
    if isinstance(raw, list):
        return PlannerRawShapeClass.JSON_ARRAY
    if isinstance(raw, str):
        return PlannerRawShapeClass.JSON_STRING
    if raw is None or isinstance(raw, int | float | bool):
        return PlannerRawShapeClass.JSON_SCALAR
    return PlannerRawShapeClass.OTHER


def stamp_retry_budget(model: Any, writer: QueryTrace | None) -> None:
    if writer is None:
        return
    for candidate in (model, getattr(model, "inner", None)):
        if candidate is None:
            continue
        budget = getattr(candidate, "max_retries", None)
        if budget is not None:
            writer.provider_http_retry_budget = int(budget)
            return
