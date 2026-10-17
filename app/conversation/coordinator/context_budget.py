"""Context budgeting, input size measurement, and deterministic pruning (R5)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.conversation.coordinator.contracts import (
    CoordinatorContext,
    HistoryLine,
    Observation,
)
from app.conversation.coordinator.prompt import CoordinatorPrompt, render_history_line
from app.providers.context_budget import ProviderContextCounter


@dataclass(frozen=True)
class ContextBudget:
    """Declared and proposed context budget ceilings."""

    total_tokens: int = 20_000
    total_bytes: int = 65_536
    observations_tokens: int = 8_000
    business_observation_tokens: int = 3_500
    document_observation_tokens: int = 2_500
    restored_sources_tokens: int = 8_000
    history_tokens: int = 6_000
    output_reserve_tokens: int = 0
    margin_tokens: int = 1_024


@dataclass(frozen=True)
class BudgetedModelInput:
    messages: tuple[BaseMessage, ...]
    tools: tuple[dict[str, object], ...]
    tokens: int
    bytes: int
    retained_evidence_ids: tuple[str, ...]
    omissions: tuple[str, ...]


@dataclass(frozen=True)
class ContextTooLarge:
    tokens: int
    bytes: int
    mandatory_only: bool


class ContextBudgetExceeded(Exception):
    """Raised when input exceeds context budget limits."""

    def __init__(self, tokens: int, bytes: int, mandatory_only: bool) -> None:
        super().__init__(
            f"Context budget exceeded: {tokens} tokens, {bytes} bytes "
            f"(mandatory_only={mandatory_only})"
        )
        self.tokens = tokens
        self.bytes = bytes
        self.mandatory_only = mandatory_only


def _observation_tokens(obs: Observation, counter: ProviderContextCounter) -> int:
    content = obs.summary
    if obs.rows:
        content += "\n" + json.dumps(list(obs.rows), ensure_ascii=False)
        if obs.row_identity:
            content += "\n" + json.dumps(obs.row_identity)
    return counter.measure([HumanMessage(content=content)], ()).tokens


def _build_action_tools(allowed_actions: frozenset[str]) -> tuple[dict[str, Any], ...]:
    from app.conversation.coordinator.model import ACTION_SCHEMAS

    tools_list: list[dict[str, Any]] = []
    for schema in ACTION_SCHEMAS:
        name = schema.model_config.get("title") or schema.__name__
        if name in allowed_actions or schema.__name__ in allowed_actions:
            tools_list.append(convert_to_openai_tool(schema))
    return tuple(tools_list)


def _prune_history(
    history: Sequence[HistoryLine],
    budget_tokens: int,
    counter: ProviderContextCounter,
    omissions: list[str],
) -> list[HistoryLine]:
    retained = list(history)
    while retained:
        msgs = [HumanMessage(content=render_history_line(h)) for h in retained]
        if counter.measure(msgs, ()).tokens <= budget_tokens:
            break
        pruned = retained.pop(0)
        omissions.append(f"history:{pruned.exchange_id}")
    return retained


def _prune_observations_to_kind_budgets(
    observations: Sequence[Observation],
    budget: ContextBudget,
    counter: ProviderContextCounter,
    omissions: list[str],
) -> list[Observation]:
    retained_obs = list(observations)
    while retained_obs:
        bus_tokens = sum(
            _observation_tokens(o, counter) for o in retained_obs if o.kind == "query_business"
        )
        doc_tokens = sum(
            counter.measure([HumanMessage(content=o.summary)], ()).tokens
            for o in retained_obs
            if o.kind == "search_documents"
        )
        restore_tokens = sum(
            _observation_tokens(o, counter) for o in retained_obs if o.kind == "explain_sources"
        )
        total_obs_tokens = bus_tokens + doc_tokens + restore_tokens

        if (
            bus_tokens <= budget.business_observation_tokens
            and doc_tokens <= budget.document_observation_tokens
            and restore_tokens <= budget.restored_sources_tokens
            and total_obs_tokens <= budget.observations_tokens
        ):
            break

        row_idx = next(
            (
                i
                for i, o in enumerate(retained_obs)
                if o.kind in ("query_business", "explain_sources") and o.rows
            ),
            None,
        )
        if row_idx is not None and (
            bus_tokens > budget.business_observation_tokens
            or restore_tokens > budget.restored_sources_tokens
            or total_obs_tokens > budget.observations_tokens
        ):
            pruned_obs = retained_obs[row_idx]
            retained_obs[row_idx] = pruned_obs.model_copy(update={"rows": ()})
            omissions.append(f"obs:{pruned_obs.action_id}:rows")
            continue

        pruned_obs = retained_obs.pop(0)
        omissions.append(f"obs:{pruned_obs.action_id}")
    return retained_obs


def _prune_one_step(
    retained_history: list[HistoryLine],
    retained_obs: list[Observation],
    omissions: list[str],
) -> bool:
    if retained_history:
        pruned = retained_history.pop(0)
        omissions.append(f"history:{pruned.exchange_id}")
        return True
    row_idx = next(
        (
            i
            for i, o in enumerate(retained_obs)
            if o.kind in ("query_business", "explain_sources") and o.rows
        ),
        None,
    )
    if row_idx is not None:
        pruned_obs = retained_obs[row_idx]
        retained_obs[row_idx] = pruned_obs.model_copy(update={"rows": ()})
        omissions.append(f"obs:{pruned_obs.action_id}:rows")
        return True
    if retained_obs:
        pruned_obs = retained_obs.pop(0)
        omissions.append(f"obs:{pruned_obs.action_id}")
        return True
    return False


def build_budgeted_input(
    context: CoordinatorContext,
    observations: Sequence[Observation],
    *,
    prompt: CoordinatorPrompt,
    allowed_actions: frozenset[str],
    counter: ProviderContextCounter,
    budget: ContextBudget = ContextBudget(),
) -> BudgetedModelInput | ContextTooLarge:
    """Build and measure budgeted model input, pruning optional items deterministically."""
    effective_max_tokens = min(
        budget.total_tokens,
        counter.capacity_tokens - budget.output_reserve_tokens - budget.margin_tokens,
    )
    effective_max_bytes = budget.total_bytes
    tools = _build_action_tools(allowed_actions)

    # First check mandatory content alone
    mandatory_context = context.model_copy(update={"history": ()})
    mandatory_messages = prompt.render(
        mandatory_context,
        (),
        allowed_actions=allowed_actions,
    )
    mandatory_measure = counter.measure(mandatory_messages, tools)
    if (
        mandatory_measure.tokens > effective_max_tokens
        or mandatory_measure.bytes > effective_max_bytes
    ):
        return ContextTooLarge(
            tokens=mandatory_measure.tokens,
            bytes=mandatory_measure.bytes,
            mandatory_only=True,
        )

    omissions: list[str] = []
    retained_history = _prune_history(context.history, budget.history_tokens, counter, omissions)
    retained_obs = _prune_observations_to_kind_budgets(observations, budget, counter, omissions)

    # Now measure combined messages and tools against total_tokens and total_bytes
    while True:
        curr_context = context.model_copy(update={"history": tuple(retained_history)})
        curr_messages = prompt.render(
            curr_context,
            tuple(retained_obs),
            allowed_actions=allowed_actions,
        )
        measure = counter.measure(curr_messages, tools)

        if measure.tokens <= effective_max_tokens and measure.bytes <= effective_max_bytes:
            # Fits within budget
            retained_ev_ids = tuple(ev_id for o in retained_obs for ev_id in o.evidence_ids)
            return BudgetedModelInput(
                messages=tuple(curr_messages),
                tools=tools,
                tokens=measure.tokens,
                bytes=measure.bytes,
                retained_evidence_ids=retained_ev_ids,
                omissions=tuple(omissions),
            )

        if not _prune_one_step(retained_history, retained_obs, omissions):
            return ContextTooLarge(
                tokens=measure.tokens,
                bytes=measure.bytes,
                mandatory_only=True,
            )
