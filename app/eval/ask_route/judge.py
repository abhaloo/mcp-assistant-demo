"""Model-judged dimensions for the production-ask quality suite.

Only two things genuinely need a judge: whether a synthesised claim is true
against evidence the ask route never saw, and whether the answer let the user
do what they came to do. Everything else is a deterministic check.

Two design rules keep the judge honest:

* The judge reads the ORACLE evidence, never the sources the route chose. A
  judge shown the route's own retrieval grades the route against itself.
* Every score has a written anchor. A number without an anchor is a vibe.

Nothing in this module opens a network connection. The caller injects a
``complete`` callable, so the judge is exercised in tests without spend.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

JudgedDimension = Literal["accuracy", "helpfulness"]

Completion = Callable[[str, str], str]
"""(system_prompt, user_prompt) -> raw model text. Injected by the runner."""

ACCURACY_ANCHORS: dict[int, str] = {
    5: (
        "Every checkable claim in the answer matches the evidence. Numbers, names, and "
        "dates agree exactly. Nothing material is asserted that the evidence does not support."
    ),
    4: (
        "All material claims match the evidence. At most one immaterial detail is imprecise "
        "(rounding, a paraphrased label) and no decision would change because of it."
    ),
    3: (
        "The central claim matches the evidence, but at least one supporting claim is wrong, "
        "unsupported, or omitted in a way a careful reader would notice."
    ),
    2: (
        "The central claim is partly wrong, or the answer mixes supported and unsupported "
        "claims without distinguishing them."
    ),
    1: (
        "The central claim contradicts the evidence, or the answer asserts facts the evidence "
        "does not contain at all."
    ),
}

HELPFULNESS_ANCHORS: dict[int, str] = {
    5: (
        "The user can act immediately. Every item under 'what the user must be able to do' is "
        "satisfied by the answer alone, in a form they can read without re-asking."
    ),
    4: (
        "The user can act after one trivial step (reading one more row, doing one obvious sum). "
        "No listed need is unmet."
    ),
    3: (
        "One listed need is unmet, or the answer is correct but buried — the user must re-read "
        "or re-ask to get at it."
    ),
    2: (
        "Two or more listed needs are unmet, or the answer answers a nearby question rather "
        "than the one asked."
    ),
    1: (
        "The answer does not address the stated need at all, or it refuses without saying what "
        "the user could do instead."
    ),
}

_SYSTEM_PROMPT = (
    "You grade one answer from an internal business assistant. You are given the question, "
    "the answer, and independently-derived evidence. Grade only against that evidence. "
    "You never see the documents the assistant retrieved, and you must not assume any fact "
    "that is not in the evidence. Reply with one JSON object and nothing else."
)


class JudgeVerdict(BaseModel):
    """One judged score with the quotation that justifies it."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    evidence_quote: str = Field(
        default="",
        description="Verbatim span from the evidence, or from the answer for helpfulness.",
    )
    reason: str = Field(default="", max_length=600)


@dataclass(frozen=True)
class JudgeRequest:
    case_id: str
    dimension: JudgedDimension
    question: str
    answer: str
    evidence: str
    claim: str | None = None
    user_need: str | None = None
    must_enable: tuple[str, ...] = ()


def _anchor_block(anchors: dict[int, str]) -> str:
    return "\n".join(f"{score} — {text}" for score, text in sorted(anchors.items(), reverse=True))


def build_prompt(request: JudgeRequest) -> tuple[str, str]:
    """The system and user prompt for one judged dimension."""
    if request.dimension == "accuracy":
        body = (
            f"QUESTION:\n{request.question}\n\n"
            f"CLAIM TO VERIFY:\n{request.claim or 'every checkable claim in the answer'}\n\n"
            f"INDEPENDENT EVIDENCE:\n{request.evidence}\n\n"
            f"ANSWER:\n{request.answer}\n\n"
            f"SCALE:\n{_anchor_block(ACCURACY_ANCHORS)}\n\n"
            'Reply as {"score": <1-5>, "evidence_quote": "<verbatim span of the evidence '
            'that decided the score>", "reason": "<one sentence>"}.'
        )
    else:
        needs = "\n".join(f"- {item}" for item in request.must_enable) or "- (none listed)"
        body = (
            f"QUESTION:\n{request.question}\n\n"
            f"WHAT THE USER CAME FOR:\n{request.user_need or request.question}\n\n"
            f"WHAT THE USER MUST BE ABLE TO DO AFTER READING:\n{needs}\n\n"
            f"ANSWER:\n{request.answer}\n\n"
            f"SCALE:\n{_anchor_block(HELPFULNESS_ANCHORS)}\n\n"
            'Reply as {"score": <1-5>, "evidence_quote": "<verbatim span of the answer that '
            'decided the score>", "reason": "<one sentence>"}.'
        )
    return _SYSTEM_PROMPT, body


class JudgeProtocolError(ValueError):
    """The judge returned something that is not a verdict."""


def parse_verdict(raw: str) -> JudgeVerdict:
    """Parse the judge reply, failing loudly rather than defaulting to a score."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise JudgeProtocolError(f"no JSON object in judge reply: {raw[:200]!r}")
    try:
        return JudgeVerdict.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise JudgeProtocolError(f"unparsable judge verdict: {exc}") from exc


def judge_one(request: JudgeRequest, complete: Completion) -> JudgeVerdict:
    system, user = build_prompt(request)
    return parse_verdict(complete(system, user))


@dataclass(frozen=True)
class CanaryCase:
    """A pinned answer whose score is already agreed, for testing the judge."""

    canary_id: str
    dimension: JudgedDimension
    question: str
    answer: str
    evidence: str
    expected_score: int
    claim: str | None = None
    user_need: str | None = None
    must_enable: tuple[str, ...] = ()


@dataclass(frozen=True)
class CanaryOutcome:
    canary_id: str
    expected_score: int
    actual_score: int

    @property
    def within_tolerance(self) -> bool:
        return abs(self.expected_score - self.actual_score) <= 1


def run_judge_canary(cases: list[CanaryCase], complete: Completion) -> list[CanaryOutcome]:
    """Score the pinned canary answers. A run whose canary drifts is void.

    Tolerance is one point: the anchors are written to be reproducible, but a
    judge that disagrees by two points on a pinned answer is not measuring the
    same thing the rubric describes, so its verdicts on real cases mean nothing.
    """
    outcomes: list[CanaryOutcome] = []
    for case in cases:
        verdict = judge_one(
            JudgeRequest(
                case_id=case.canary_id,
                dimension=case.dimension,
                question=case.question,
                answer=case.answer,
                evidence=case.evidence,
                claim=case.claim,
                user_need=case.user_need,
                must_enable=case.must_enable,
            ),
            complete,
        )
        outcomes.append(
            CanaryOutcome(
                canary_id=case.canary_id,
                expected_score=case.expected_score,
                actual_score=verdict.score,
            )
        )
    return outcomes


def canary_holds(outcomes: list[CanaryOutcome]) -> bool:
    return bool(outcomes) and all(outcome.within_tolerance for outcome in outcomes)


def load_canary(path: str | Path) -> list[CanaryCase]:
    """Load the pinned canary set. Scores in the file are the agreed labels."""
    cases: list[CanaryCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        cases.append(
            CanaryCase(
                canary_id=record["canary_id"],
                dimension=record["dimension"],
                question=record["question"],
                answer=record["answer"],
                evidence=record["evidence"],
                expected_score=int(record["expected_score"]),
                claim=record.get("claim"),
                user_need=record.get("user_need"),
                must_enable=tuple(record.get("must_enable", ())),
            )
        )
    if not cases:
        raise ValueError(f"{path}: no canary cases")
    return cases
