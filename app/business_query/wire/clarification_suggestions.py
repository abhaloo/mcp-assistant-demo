"""LLM options for a planner stall or an empty planner clarify.

Empty output is a typed timeout, never an empty card. Static radios are out.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.business_query.outcomes import ClarificationRequired, Incomplete
from app.business_query.ports import ClarificationChoiceSuggester
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import TurnBudget, allowance_seconds, await_with_budget
from app.providers.model_purpose import ModelPurpose

logger = logging.getLogger(__name__)

SUGGESTION_PICK_CAP = 3

_SYSTEM = (
    "The previous planning step ran out of time, or it asked a clarifying "
    "question without options. Propose 1 to 3 selectable follow-up options "
    "that would make the next attempt succeed. Base every option only on the "
    "user's question and any dialogue. Do not invent a fixed menu of customer, "
    "status, or date. Each option needs id, a short label, and rewrite: a "
    "standalone question the system can plan."
)


class SuggestedChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    rewrite: str


class SuggestionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: list[SuggestedChoice] = Field(default_factory=list)


def _normalize_choices(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, SuggestionPayload):
        rows = raw.choices
    elif isinstance(raw, dict):
        rows = SuggestionPayload.model_validate(raw).choices
    else:
        return []
    filled: list[dict[str, Any]] = []
    for choice in rows:
        identifier = str(choice.id).strip()
        label = str(choice.label).strip()
        rewrite = str(choice.rewrite).strip()
        if not identifier or not label or not rewrite:
            continue
        filled.append({"id": identifier, "label": label, "rewrite": rewrite})
        if len(filled) >= SUGGESTION_PICK_CAP:
            break
    return filled


class LlmClarificationChoiceSuggester:
    """Conversation-route structured call. Failure and empty both mean no card."""

    def __init__(self, model_factory: Callable[[], Any] | None = None) -> None:
        self._model_factory = model_factory or _conversation_model

    async def suggest(
        self,
        *,
        question: str,
        prompt: str,
        dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None,
        timeout_seconds: float,
        turn_budget: TurnBudget,
    ) -> list[dict[str, Any]]:
        if timeout_seconds <= 0:
            return []
        history = ""
        if dialogue:
            history = "\n".join(f"{role}: {text}" for role, text in dialogue)
        human = f"Question: {question}\nPrompt: {prompt}"
        if history:
            human = f"{human}\nDialogue:\n{history}"
        try:
            # Building the call is inside the degrade path too: a model that
            # cannot bind structured output means no choices, not a failed turn.
            model = self._model_factory()
            bound = model.with_structured_output(SuggestionPayload, method="json_schema")
            raw = await await_with_budget(
                lambda: bound.ainvoke(
                    [SystemMessage(content=_SYSTEM), HumanMessage(content=human)]
                ),
                turn_budget,
                ceiling_seconds=timeout_seconds,
                reserve_seconds=0.0,
            )
        except (TimeoutError, DeadlineExpiredError):
            return []
        except Exception:
            logger.exception("clarification suggestion call failed")
            return []
        return _normalize_choices(raw)


def _conversation_model() -> Any:
    from app.providers import get_chat_model

    return get_chat_model(purpose=ModelPurpose.conversation, temperature=0)


@dataclass(frozen=True)
class SuggestionContext:
    """Inputs for one budget-aware suggestion call."""

    user_question: str
    dialogue: Sequence[tuple[Literal["ai", "human"], str]] | None
    turn_budget: TurnBudget
    terminal_reserve_seconds: float
    suggester: ClarificationChoiceSuggester | None


async def clarification_with_suggestions(
    *,
    question: str,
    continuation: str,
    context: SuggestionContext,
) -> ClarificationRequired | Incomplete:
    """Build a clarification card from the LLM, or a timeout if none land."""
    timeout = allowance_seconds(
        context.turn_budget, reserve_seconds=context.terminal_reserve_seconds
    )
    if timeout <= 0 or context.suggester is None:
        return Incomplete(reason_code="timeout")
    choices = await context.suggester.suggest(
        question=context.user_question,
        prompt=question,
        dialogue=context.dialogue,
        timeout_seconds=timeout,
        turn_budget=context.turn_budget,
    )
    return ClarificationRequired(
        question=question,
        continuation=continuation,
        choices=choices or [],
        allow_free_text=True,
    )
