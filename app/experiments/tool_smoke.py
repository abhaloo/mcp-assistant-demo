"""Deterministic native-tool loop oracle for DeepSeek-style smoke (offline-first)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from app.providers.reasoning import should_normalize_final

FINAL_MARKER = "SMOKE_LOOP_COMPLETE"

_REASONING_TOKENS_PRESENT_BUT_MISSING = (
    "reasoning tokens present but fields missing on tool-call turn"
)
_REASONING_STRIPPED_ON_TOOL_CALL_TURN = "reasoning stripped on intermediate tool-call turn"


@dataclass(frozen=True)
class ToolSmokeResult:
    """Redacted outcome of a deterministic tool-loop smoke run."""

    passed: bool
    tool_round_trips: int
    required_turns: int
    reasoning_preserved_all_intermediate: bool
    final_answer_normalized: bool
    evidence: dict[str, Any]
    error: str | None = None


@tool
def smoke_step(step: int) -> str:
    """Return the completed step number for the deterministic tool loop."""
    return str(step)


def _assistant_has_reasoning(msg: BaseMessage) -> bool:
    kwargs = msg.additional_kwargs or {}
    return any(
        bool(kwargs.get(key)) for key in ("reasoning_content", "reasoning", "reasoning_details")
    )


def _final_answer_clean(msg: BaseMessage) -> bool:
    if not should_normalize_final(msg):
        return False
    content = getattr(msg, "content", "")
    return (
        isinstance(content, str)
        and content.strip() == FINAL_MARKER
        and not _assistant_has_reasoning(msg)
    )


def _redacted_evidence(
    *,
    tool_round_trips: int,
    required_turns: int,
    reasoning_preserved_all_intermediate: bool,
    final_answer_normalized: bool,
    assistant_message_count: int,
    tool_message_count: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "tool_round_trips": tool_round_trips,
        "required_turns": required_turns,
        "reasoning_preserved_all_intermediate": reasoning_preserved_all_intermediate,
        "final_answer_normalized": final_answer_normalized,
        "assistant_message_count": assistant_message_count,
        "tool_message_count": tool_message_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "turns": turns or [],
    }


def _tokens_from_message(msg: BaseMessage) -> tuple[int, int]:
    um = getattr(msg, "usage_metadata", None) or {}
    if not isinstance(um, dict):
        um = dict(um) if hasattr(um, "items") else {}
    return int(um.get("input_tokens") or 0), int(um.get("output_tokens") or 0)


def _validate_tool_call_turn(message: AIMessage, *, expected_step: int) -> str | None:
    """Check one tool-call turn against every invariant; ``None`` when it is valid.

    Called once the turn is confirmed to be an ``AIMessage`` and already appended to
    the transcript -- a turn with no tool calls at all is also valid here, since the
    caller then treats it as a candidate final answer, a check this function does
    not own.
    """
    tool_calls = message.tool_calls or []
    if not tool_calls:
        return None

    has_reasoning = _assistant_has_reasoning(message)
    if not has_reasoning:
        usage = getattr(message, "usage_metadata", None) or {}
        reasoning_tokens = 0
        if isinstance(usage, dict):
            otd = usage.get("output_token_details") or {}
            if isinstance(otd, dict):
                reasoning_tokens = int(otd.get("reasoning") or 0)
        if reasoning_tokens > 0:
            return _REASONING_TOKENS_PRESENT_BUT_MISSING
        if not usage:
            # Offline fakes have no usage_metadata — missing fields mean strip/regression.
            return _REASONING_STRIPPED_ON_TOOL_CALL_TURN

    if len(tool_calls) != 1:
        return f"expected exactly one tool call, got {len(tool_calls)}"
    tc = tool_calls[0]
    if tc.get("name") != smoke_step.name:
        return f"expected tool {smoke_step.name}, got {tc.get('name')}"
    args = tc.get("args")
    if not isinstance(args, dict) or set(args) != {"step"}:
        return "smoke_step arguments must contain only step"
    try:
        actual_step = int(args["step"])
    except (TypeError, ValueError):
        return f"expected integer step {expected_step}, got {args.get('step')!r}"
    if actual_step != expected_step:
        return f"expected step {expected_step}, got {actual_step}"
    return None


def _turn_evidence(message: AIMessage) -> dict[str, Any]:
    """Redacted evidence for one completed, already-validated tool-call turn."""
    tc = message.tool_calls[0]
    tool_result = smoke_step.invoke(tc["args"])
    input_tokens, output_tokens = _tokens_from_message(message)
    response_metadata = getattr(message, "response_metadata", None) or {}
    if not isinstance(response_metadata, dict):
        response_metadata = {}
    return {
        "step": int(tc["args"]["step"]),
        "tool_name": tc["name"],
        "tool_call_id": tc["id"],
        "tool_result": str(tool_result),
        "reasoning_present": _assistant_has_reasoning(message),
        "request_id": str(getattr(message, "id", None) or "unavailable"),
        "actual_model": str(
            response_metadata.get("model_name") or response_metadata.get("model") or "unknown"
        ),
        "actual_provider": str(response_metadata.get("provider") or "openrouter"),
        "finish_reason": str(response_metadata.get("finish_reason") or "tool_calls"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def run_tool_smoke(llm: Any, *, required_turns: int = 5) -> ToolSmokeResult:
    """Run a bounded tool loop and verify replay + final-answer normalization oracles."""
    if required_turns < 1:
        raise ValueError("required_turns must be at least 1")

    bound = llm.bind_tools([smoke_step])
    messages: list[BaseMessage] = [
        HumanMessage(
            content=(
                f"Run the smoke_step tool exactly {required_turns} times, once per turn, "
                "using consecutive step values starting at 1 and ending at "
                f"{required_turns}. After you receive tool result '{required_turns}', "
                f"stop calling tools and reply with exactly {FINAL_MARKER}."
            )
        ),
    ]
    tool_round_trips = 0
    reasoning_preserved_all_intermediate = True
    final_answer_normalized = False
    error: str | None = None
    input_tokens = 0
    output_tokens = 0
    turn_evidence: list[dict[str, Any]] = []

    max_invocations = required_turns + 2
    for _ in range(max_invocations):
        response = bound.invoke(messages)
        if not isinstance(response, AIMessage):
            error = f"expected AIMessage, got {type(response).__name__}"
            break

        tin, tout = _tokens_from_message(response)
        input_tokens += tin
        output_tokens += tout
        messages.append(response)

        reason = _validate_tool_call_turn(response, expected_step=tool_round_trips + 1)
        if reason is not None:
            if reason in (
                _REASONING_TOKENS_PRESENT_BUT_MISSING,
                _REASONING_STRIPPED_ON_TOOL_CALL_TURN,
            ):
                reasoning_preserved_all_intermediate = False
            error = reason
            break

        tool_calls = response.tool_calls or []
        if tool_calls:
            turn = _turn_evidence(response)
            turn_evidence.append(turn)
            tc = tool_calls[0]
            messages.append(
                ToolMessage(content=turn["tool_result"], tool_call_id=tc["id"], name=tc["name"])
            )
            tool_round_trips += 1
            continue

        final_answer_normalized = _final_answer_clean(response)
        if not final_answer_normalized:
            error = f"final answer must be the clean literal {FINAL_MARKER}"
        break
    else:
        error = error or "loop exhausted without final answer"

    if tool_round_trips < required_turns and error is None:
        error = f"expected {required_turns} tool round trips, got {tool_round_trips}"

    assistant_message_count = sum(1 for m in messages if isinstance(m, AIMessage))
    tool_message_count = sum(1 for m in messages if isinstance(m, ToolMessage))

    passed = (
        error is None
        and tool_round_trips == required_turns
        and reasoning_preserved_all_intermediate
        and final_answer_normalized
    )

    return ToolSmokeResult(
        passed=passed,
        tool_round_trips=tool_round_trips,
        required_turns=required_turns,
        reasoning_preserved_all_intermediate=reasoning_preserved_all_intermediate,
        final_answer_normalized=final_answer_normalized,
        evidence=_redacted_evidence(
            tool_round_trips=tool_round_trips,
            required_turns=required_turns,
            reasoning_preserved_all_intermediate=reasoning_preserved_all_intermediate,
            final_answer_normalized=final_answer_normalized,
            assistant_message_count=assistant_message_count,
            tool_message_count=tool_message_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            turns=turn_evidence,
        ),
        error=error,
    )
