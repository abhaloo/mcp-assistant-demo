"""Shared SQL clarify seam — same loop for prod and eval; only the reply source differs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

CLARIFY_PREFIX = "CLARIFY:"


def is_clarification_answer(text: str) -> bool:
    """True when the model used the shared clarify marker (Contract shared-clarify)."""
    return (text or "").lstrip().upper().startswith(CLARIFY_PREFIX.upper())


def clarification_question_text(text: str) -> str:
    """Strip the marker for display / reply-source context."""
    raw = (text or "").lstrip()
    if raw.upper().startswith(CLARIFY_PREFIX.upper()):
        return raw[len(CLARIFY_PREFIX) :].lstrip()
    return raw


class ClarificationReplySource(Protocol):
    def reply(self, clarification_question: str) -> str | None:
        """Next user message, or None to stop (no further clarify turns)."""


class NullClarificationReplySource:
    """Prod default until Ask UI can supply a user reply — same wiring, no auto-answer."""

    def reply(self, clarification_question: str) -> str | None:
        return None


class OracleClarificationReplySource:
    """Eval: canned business answers (never gold SQL). One reply per clarify by default."""

    def __init__(self, answers: Sequence[str], *, max_replies: int = 1) -> None:
        self._answers = [a for a in answers if (a or "").strip()]
        self._max_replies = max_replies
        self._used = 0

    def reply(self, clarification_question: str) -> str | None:
        if self._used >= self._max_replies or not self._answers:
            return None
        self._used += 1
        return self._answers.pop(0)


@dataclass
class ClarifyRunResult:
    output: str
    queries: list[str]
    clarifications: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    n_clarifications: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    deanonymize: Any = None
    execution_records: list[Any] = field(default_factory=list)
    sql_stop_reason: str | None = None


# turn(input=..., prior_messages=..., config=...) -> agent result dict with messages
TurnFn = Callable[..., dict[str, Any]]


def run_with_clarifications(
    turn: TurnFn,
    question: str,
    reply_source: ClarificationReplySource,
    *,
    max_clarifications: int = 1,
    config: Any = None,
) -> ClarifyRunResult:
    """Drive the shared clarify loop (prod Null source / eval Oracle — same function)."""
    clarifications: list[str] = []
    replies: list[str] = []
    result = turn(input=question, prior_messages=None, config=config)

    while len(clarifications) < max_clarifications and is_clarification_answer(
        str(result.get("output") or "")
    ):
        q_text = clarification_question_text(str(result["output"]))
        clarifications.append(q_text)
        user_reply = reply_source.reply(q_text)
        if user_reply is None:
            break
        replies.append(user_reply)
        prior = list(result["messages"])
        result = turn(input=user_reply, prior_messages=prior, config=config)

    return ClarifyRunResult(
        output=str(result.get("output") or ""),
        queries=list(result.get("queries") or []),
        clarifications=clarifications,
        replies=replies,
        n_clarifications=len(clarifications),
        raw=dict(result.get("raw") or {}),
        deanonymize=result.get("deanonymize"),
        execution_records=list(result.get("execution_records") or []),
        sql_stop_reason=result.get("sql_stop_reason"),
    )
