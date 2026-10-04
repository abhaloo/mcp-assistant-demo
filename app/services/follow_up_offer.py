"""Follow-up offers: the one tool an answer model may call, and the gate that
decides whether the offer becomes a card (ADR 0068).

The tool records a proposal only. It never runs a query, calls Billing, or
sends a second question. Validation rejects the whole offer on the first bad
action; the answer completes either way.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from app.auth import Principal
from app.conversation.coordinator.draft_checks import (
    names_a_record_type,
    not_a_next_question,
)
from app.models.ask_v2_events import FollowUpAction, FollowUpOffer
from app.policy.manifest_loader import load_manifest
from app.services.follow_up_suggestions import reachable_resources
from app.telemetry.metrics import record_follow_up_suggestion_decision

logger = logging.getLogger(__name__)

OFFER_FOLLOW_UP = "offer_follow_up"

_MAX_OFFER_ACTIONS: int = 3

_DESCRIPTION = (
    "Offer one to three optional next questions the reader could ask about the "
    "records or documents you just used. Call it at most once, and only after "
    "you have written the complete answer. Do not repeat the current question, "
    "do not offer pagination or 'more results', and do not name records the "
    "reader has not seen. The offer never runs anything."
)
_PAGINATION = re.compile(
    r"\b(next|more|another|previous)\s+(page|results?|rows?|records?)\b|\bpage\s+\d+\b|\b(show|load)\s+more\b",
    re.IGNORECASE,
)
_TERMINAL_PUNCTUATION = ".?!…;:,"


def offer_follow_up_tool() -> dict[str, Any]:
    tool = convert_to_openai_tool(FollowUpOffer)
    tool["function"]["name"] = OFFER_FOLLOW_UP
    tool["function"]["description"] = _DESCRIPTION
    return tool


def normalize_prompt(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    collapsed = " ".join(folded.split())
    return collapsed.rstrip(_TERMINAL_PUNCTUATION).strip()


@dataclass
class FollowUpCapture:
    """Request-scoped record of the tool calls one generation produced.

    Streamed tool calls arrive as fragments keyed by index; the name travels on
    the first fragment and the argument string is concatenated across them.
    """

    _names: dict[int, str] = field(default_factory=dict)
    _args: dict[int, list[str]] = field(default_factory=dict)

    def record_chunk(self, chunk: object) -> None:
        fragments = getattr(chunk, "tool_call_chunks", None)
        if not isinstance(fragments, list):
            return
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            index = fragment.get("index")
            if not isinstance(index, int):
                index = len(self._names)
            name = fragment.get("name")
            if isinstance(name, str) and name:
                self._names.setdefault(index, name)
            args = fragment.get("args")
            if isinstance(args, str):
                self._args.setdefault(index, []).append(args)

    def calls(self) -> list[str]:
        return [
            "".join(self._args.get(index, []))
            for index in sorted(self._names)
            if self._names[index] == OFFER_FOLLOW_UP
        ]

    def tool_calls(self) -> list[dict[str, Any]]:
        """Every call the model made, as the provider would replay it."""
        out = []
        for index in sorted(self._names):
            raw = "".join(self._args.get(index, []))
            try:
                args = json.loads(raw) if raw else {}
            except ValueError:
                args = {}
            out.append(
                {
                    "id": f"call-{index}",
                    "name": self._names[index],
                    "args": args if isinstance(args, dict) else {},
                }
            )
        return out


def unreachable_record_types(principal: Principal) -> tuple[str, ...]:
    """The declared record types this caller cannot read; none without a snapshot."""
    scope = _offer_scope(principal)
    if scope is None:
        return ()
    declared, reachable = scope
    return tuple(sorted(set(declared) - set(reachable)))


def _offer_scope(principal: Principal) -> tuple[tuple[str, ...], Sequence[str]] | None:
    """The declared and the reachable record types for this caller, or ``None``.

    Without a record-access snapshot there is no way to tell a reachable record
    type from an invisible one, so no offer is made; the same rule the
    deterministic chips follow. A manifest that will not load is the
    executor's problem to report, never a reason to guess.
    """
    if getattr(principal, "resources", None) is None:
        logger.info("follow_up_offer rejected reason=no_record_access")
        return None
    try:
        manifest = load_manifest()
    except Exception:
        logger.warning("follow_up_offer rejected reason=manifest_unavailable")
        return None
    return tuple(manifest.resources), reachable_resources(manifest, principal)


def accept_for_principal(
    capture: FollowUpCapture, *, question: str, principal: Principal
) -> FollowUpOffer | None:
    """The accepted offer for this caller, or ``None``."""
    scope = _offer_scope(principal)
    if scope is None:
        return None
    declared, reachable = scope
    return accept_follow_up_offer(
        capture, question=question, declared_resources=declared, reachable_resources=reachable
    )


def accept_offer_for_principal(
    actions: Sequence[FollowUpAction], *, question: str, principal: Principal
) -> FollowUpOffer | None:
    """An offer built by the caller, judged by the gate a model's tool call passes."""
    started = time.perf_counter()
    scope = _offer_scope(principal)
    if scope is None:
        return None
    declared, reachable = scope
    accepted: list[FollowUpAction] = []
    seen: set[str] = set()

    for action in actions:
        if not_a_next_question(action.prompt):
            logger.info("follow_up_offer rejected reason=not_a_question")
            continue
        key = normalize_prompt(action.prompt)
        if key in seen:
            logger.info("follow_up_offer rejected reason=duplicate_prompt")
            continue
        single = FollowUpOffer(actions=[action])
        reason = offer_reject_reason(single, question, declared, reachable)
        if reason is not None:
            logger.info("follow_up_offer rejected reason=%s", reason)
            continue
        accepted.append(action)
        seen.add(key)
        if len(accepted) == _MAX_OFFER_ACTIONS:
            break

    mode = "offer_accepted" if accepted else "offer_none"
    record_follow_up_suggestion_decision(mode=mode, seconds=time.perf_counter() - started)
    if not accepted:
        return None
    return FollowUpOffer(actions=accepted)


def accept_follow_up_offer(
    capture: FollowUpCapture,
    *,
    question: str,
    declared_resources: Sequence[str],
    reachable_resources: Sequence[str],
) -> FollowUpOffer | None:
    """The single valid offer for this turn, or ``None``.

    The rejection reason is logged as a closed token. Prompt text is never
    logged.
    """
    started = time.perf_counter()
    calls = capture.calls()
    reason = _reject_reason(calls, question, declared_resources, reachable_resources)
    if reason is not None:
        logger.info("follow_up_offer rejected reason=%s", reason)
        record_follow_up_suggestion_decision(
            mode=f"offer_{reason}", seconds=time.perf_counter() - started
        )
        return None
    offer = FollowUpOffer.model_validate(json.loads(calls[0]))
    record_follow_up_suggestion_decision(
        mode="offer_accepted", seconds=time.perf_counter() - started
    )
    return offer


def _reject_reason(
    calls: list[str],
    question: str,
    declared: Sequence[str],
    reachable: Sequence[str],
) -> str | None:
    if not calls:
        return "none"
    if len(calls) > 1:
        return "duplicate_call"
    try:
        offer = FollowUpOffer.model_validate(json.loads(calls[0]))
    except (ValueError, ValidationError):
        return "malformed"
    return offer_reject_reason(offer, question, declared, reachable)


def offer_reject_reason(
    offer: FollowUpOffer,
    question: str,
    declared: Sequence[str],
    reachable: Sequence[str],
) -> str | None:
    """Why this offer cannot be shown, or None."""
    current = normalize_prompt(question)
    unreachable = tuple(sorted(set(declared) - set(reachable)))
    seen_prompts: set[str] = set()
    seen_ids: set[str] = set()
    for action in offer.actions:
        prompt = normalize_prompt(action.prompt)
        if prompt == current:
            return "repeats_question"
        if _PAGINATION.search(action.prompt):
            return "pagination"
        if names_a_record_type(action.prompt, unreachable):
            return "unreachable_resource"
        if prompt in seen_prompts or action.id in seen_ids:
            return "duplicate_action"
        seen_prompts.add(prompt)
        seen_ids.add(action.id)
    return None


def needs_continuation(capture: FollowUpCapture, streamed_parts: Sequence[str]) -> bool:
    """A response that called a tool and wrote nothing is not an answer yet."""
    return bool(capture.tool_calls()) and not "".join(streamed_parts).strip()


def continuation_messages(
    prompt_value: PromptValue, capture: FollowUpCapture, *, accepted: bool
) -> list[BaseMessage]:
    """The conversation to finish an answer after a tool-only response: the
    original prompt, the model's calls, and one result per call."""
    calls = capture.tool_calls()
    verdict = "accepted" if accepted else "rejected"
    return [
        *prompt_value.to_messages(),
        AIMessage(
            content="",
            tool_calls=[{"id": c["id"], "name": c["name"], "args": c["args"]} for c in calls],
        ),
        *[
            ToolMessage(
                content=verdict if c["name"] == OFFER_FOLLOW_UP else "rejected",
                tool_call_id=c["id"],
            )
            for c in calls
        ],
    ]
