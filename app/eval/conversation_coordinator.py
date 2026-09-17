"""Conversational coordinator evaluation schema, scoring rubric, and models.

Evaluates coordinator turns across 6 strata (general, explanation, fresh_bq,
documents_mixed, ambiguity_regeneration_focus, failure_access_budget) on three
independent axes: trajectory, sources, and grounding.

Follows ADR 0066: turns an executed coordinator turn into a typed score record.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.conversation.coordinator.contracts import (
    ActionKind,
    CoordinatorContext,
    QuestionOrigin,
)
from app.conversation.coordinator.policy import CoordinatorPolicy
from app.conversation.coordinator.runtime import (
    BusinessQueryTerminal,
    ClarifyRequested,
    FinishedDraft,
    Stopped,
)

Stratum = Literal[
    "general",
    "explanation",
    "fresh_bq",
    "documents_mixed",
    "ambiguity_regeneration_focus",
    "failure_access_budget",
]
Step = ActionKind | Literal["finish_answer", "clarify"]
Axis = Literal["pass", "fail", "unproven"]

TOOL_ACTIONS: frozenset[str] = frozenset({"query_business", "search_documents", "explain_sources"})


class CoordinatorCase(BaseModel):
    """Authored frozen test case for conversational coordinator evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    stratum: Stratum
    context: CoordinatorContext
    expected_sources: tuple[str, ...] = ()
    required_actions: tuple[Step, ...]
    allowed_alternatives: tuple[tuple[Step, ...], ...] = ()
    forbidden_calls: tuple[ActionKind, ...] = ()
    expected_question_origin: QuestionOrigin | None = None
    answer_oracle: str | None = None
    # Token profile the live runner presents for this case; None means the default.
    principal: str | None = None
    provenance: str


class CaseResult(BaseModel):
    """Evaluation outcome for one case across independent axes and telemetry."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    trajectory: Axis
    sources: Axis
    grounding: Axis
    unnecessary_calls: int
    time_to_first_token_ms: int | None
    latency_ms: int | None
    tokens: int | None
    # Policy rules the observed trajectory broke; empty on a clean run.
    invariant_violations: tuple[str, ...] = ()


class CoordinatorRunOutput(BaseModel):
    """Normalized observation of a coordinator turn for evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    actions: tuple[Step, ...] = ()
    sources: tuple[str, ...] = ()
    answer_text: str = ""
    question_origin: QuestionOrigin | None = None
    unnecessary_calls: int = 0
    time_to_first_token_ms: int | None = None
    time_to_first_action_ms: int | None = None
    latency_ms: int | None = None
    tokens: int | None = None
    capture_complete: bool = True
    missing_model_usage: bool = False
    false_tool_receipts: bool = False
    status: str | None = None
    route_id: str | None = None
    model: str | None = None
    # Wire identities of the requests behind this observation; they join the
    # observation to backend capture rows and the invocation ledger.
    run_ids: tuple[str, ...] = ()
    thread_id: str | None = None
    # Why the capture is unproven, when it is; null for a complete observation.
    reason: str | None = None


def _explicit_true(value: object) -> bool:
    return value is True


def _extract_run_data(run_output: object) -> CoordinatorRunOutput:
    if isinstance(run_output, CoordinatorRunOutput):
        return run_output

    if isinstance(run_output, dict):
        return CoordinatorRunOutput(
            actions=tuple(run_output.get("actions", ())),
            sources=tuple(run_output.get("sources", ())),
            answer_text=str(run_output.get("answer_text", "")),
            question_origin=run_output.get("question_origin"),
            unnecessary_calls=int(run_output.get("unnecessary_calls", 0)),
            time_to_first_token_ms=run_output.get("time_to_first_token_ms"),
            time_to_first_action_ms=run_output.get("time_to_first_action_ms"),
            latency_ms=run_output.get("latency_ms"),
            tokens=run_output.get("tokens"),
            capture_complete=_explicit_true(run_output.get("capture_complete")),
            missing_model_usage=_explicit_true(run_output.get("missing_model_usage")),
            false_tool_receipts=_explicit_true(run_output.get("false_tool_receipts")),
            status=run_output.get("status"),
            route_id=run_output.get("route_id"),
            model=run_output.get("model"),
            run_ids=tuple(run_output.get("run_ids", ())),
            thread_id=run_output.get("thread_id"),
        )

    if isinstance(run_output, FinishedDraft):
        actions: list[Step] = [outcome.kind for outcome in run_output.outcomes]
        actions.append("finish_answer")
        answer_text = " ".join(block.text for block in run_output.draft.blocks)
        sources_set: list[str] = []
        for block in run_output.draft.blocks:
            for eid in block.evidence_ids:
                if eid not in sources_set:
                    sources_set.append(eid)
        for obs in run_output.observations:
            for eid in obs.evidence_ids:
                if eid not in sources_set:
                    sources_set.append(eid)
        question_origin: QuestionOrigin | None = None
        for obs in run_output.observations:
            if obs.question_origin:
                question_origin = obs.question_origin
                break
        return CoordinatorRunOutput(
            actions=tuple(actions),
            sources=tuple(sources_set),
            answer_text=answer_text,
            question_origin=question_origin,
            capture_complete=False,
        )

    if isinstance(run_output, ClarifyRequested):
        actions = [outcome.kind for outcome in run_output.outcomes]
        actions.append("clarify")
        return CoordinatorRunOutput(
            actions=tuple(actions),
            answer_text=run_output.question,
            capture_complete=False,
        )

    if isinstance(run_output, BusinessQueryTerminal):
        actions = [outcome.kind for outcome in run_output.outcomes]
        actions.append("clarify")
        return CoordinatorRunOutput(
            actions=tuple(actions),
            answer_text=getattr(run_output.result, "answer", "") or "",
            capture_complete=False,
        )

    if isinstance(run_output, Stopped):
        actions = [outcome.kind for outcome in run_output.outcomes]
        sources_set = []
        for obs in run_output.observations:
            for eid in obs.evidence_ids:
                if eid not in sources_set:
                    sources_set.append(eid)
        return CoordinatorRunOutput(
            actions=tuple(actions),
            sources=tuple(sources_set),
            status=run_output.reason,
            capture_complete=False,
        )

    return CoordinatorRunOutput(
        actions=tuple(getattr(run_output, "actions", ())),
        sources=tuple(getattr(run_output, "sources", ())),
        answer_text=str(getattr(run_output, "answer_text", "")),
        question_origin=getattr(run_output, "question_origin", None),
        unnecessary_calls=int(getattr(run_output, "unnecessary_calls", 0)),
        time_to_first_token_ms=getattr(run_output, "time_to_first_token_ms", None),
        time_to_first_action_ms=getattr(run_output, "time_to_first_action_ms", None),
        latency_ms=getattr(run_output, "latency_ms", None),
        tokens=getattr(run_output, "tokens", None),
        capture_complete=_explicit_true(getattr(run_output, "capture_complete", False)),
        missing_model_usage=_explicit_true(getattr(run_output, "missing_model_usage", False)),
        false_tool_receipts=_explicit_true(getattr(run_output, "false_tool_receipts", False)),
        status=getattr(run_output, "status", None),
        route_id=getattr(run_output, "route_id", None),
        model=getattr(run_output, "model", None),
    )


def oracle_literal_present(oracle: str, answer: str) -> bool:
    """True when the oracle literal appears with digit/dot boundaries.

    Prevents `60%` from matching inside `160%`.
    """
    needle = oracle.lower()
    haystack = answer.lower()
    start = 0
    while True:
        pos = haystack.find(needle, start)
        if pos < 0:
            return False
        before = haystack[pos - 1] if pos > 0 else ""
        after_index = pos + len(needle)
        after = haystack[after_index] if after_index < len(haystack) else ""
        if not before.isdigit() and not after.isdigit():
            return True
        start = pos + 1


def trajectory_invariants(actions: Sequence[Step]) -> tuple[str, ...]:
    """Policy rules every accepted trajectory obeys, independent of the case.

    Explaining restores an earlier answer, so it can only open a turn and
    nothing runs after it; one business query and two document searches are
    the ceilings (CoordinatorPolicy). An authored sequence never licenses a
    break, so a violation fails the trajectory before the case is consulted.
    """
    tools = [a for a in actions if a in TOOL_ACTIONS]
    violations: list[str] = []
    if "explain_sources" in tools[1:]:
        violations.append("explain_after_action")
    if tools and tools[0] == "explain_sources" and len(tools) > 1:
        violations.append("tool_after_explanation")
    if tools.count("query_business") > CoordinatorPolicy().max_business_queries:
        violations.append("business_query_repeated")
    if tools.count("search_documents") > CoordinatorPolicy().max_document_searches:
        violations.append("document_search_repeated")
    return tuple(violations)


def _document_search_allowed(case: CoordinatorCase) -> bool:
    """Whether any accepted trajectory for the case includes a document search."""
    sequences = (case.required_actions, *case.allowed_alternatives)
    return any("search_documents" in sequence for sequence in sequences)


def score(case: CoordinatorCase, run_output: object) -> CaseResult:
    """Score one executed turn observation against a frozen CoordinatorCase."""
    obs = _extract_run_data(run_output)

    if not obs.capture_complete or obs.status == "unproven" or obs.missing_model_usage:
        return CaseResult(
            case_id=case.case_id,
            trajectory="unproven",
            sources="unproven",
            grounding="unproven",
            unnecessary_calls=obs.unnecessary_calls,
            time_to_first_token_ms=obs.time_to_first_token_ms,
            latency_ms=obs.latency_ms,
            tokens=obs.tokens,
        )

    allowed_sequences = [case.required_actions] + list(case.allowed_alternatives)
    actual_tool_count = sum(1 for a in obs.actions if a in TOOL_ACTIONS)
    sequence_matches = tuple(obs.actions) in allowed_sequences
    if sequence_matches:
        total_unnecessary = obs.unnecessary_calls
    else:
        required_tools = sum(1 for a in case.required_actions if a in TOOL_ACTIONS)
        excess_tools = max(0, actual_tool_count - required_tools)
        total_unnecessary = max(excess_tools, obs.unnecessary_calls)

    forbidden_executed = any(action in case.forbidden_calls for action in obs.actions)
    violations = trajectory_invariants(obs.actions)
    trajectory_fails = False
    if forbidden_executed or violations:
        trajectory_fails = True
    if obs.false_tool_receipts:
        trajectory_fails = True
    if (
        case.expected_question_origin is not None
        and obs.question_origin != case.expected_question_origin
    ):
        trajectory_fails = True
    if not sequence_matches:
        trajectory_fails = True
    if total_unnecessary > 0:
        trajectory_fails = True

    trajectory_axis: Axis = "fail" if trajectory_fails else "pass"

    if not case.expected_sources and _document_search_allowed(case):
        # The case allows a document search but froze no published identities
        # to compare against: the axis is unjudgeable, not failed.
        sources_axis: Axis = "unproven"
    elif set(obs.sources) == set(case.expected_sources):
        sources_axis = "pass"
    else:
        sources_axis = "fail"

    if forbidden_executed or obs.false_tool_receipts:
        grounding_axis: Axis = "fail"
    elif case.answer_oracle is None:
        grounding_axis = "unproven"
    elif oracle_literal_present(case.answer_oracle, obs.answer_text):
        grounding_axis = "pass"
    else:
        grounding_axis = "fail"

    return CaseResult(
        case_id=case.case_id,
        trajectory=trajectory_axis,
        sources=sources_axis,
        grounding=grounding_axis,
        unnecessary_calls=total_unnecessary,
        time_to_first_token_ms=obs.time_to_first_token_ms,
        latency_ms=obs.latency_ms,
        tokens=obs.tokens,
        invariant_violations=violations,
    )


def load_coordinator_cases(path: Path | str) -> list[CoordinatorCase]:
    """Loads and validates coordinator eval cases from a JSONL file."""
    cases: list[CoordinatorCase] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            cases.append(CoordinatorCase.model_validate_json(line_str))
    return cases
