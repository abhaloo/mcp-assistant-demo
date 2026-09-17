"""Turn thoughts adapter and identifier replacement for copy hygiene."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.business_query.ports import ProgressStage
from app.services.ask_frames import AskStage

if TYPE_CHECKING:
    from app.business_query.ports import CommittedTable
    from app.services.coordinator_tools import CoordinatorProgress

_PLAIN_WORDS: dict[str, str] = {
    "query_business": "the business data",
    "QueryBusiness": "the business data",
    "search_documents": "the company documents",
    "SearchDocuments": "the company documents",
    "explain_sources": "explain the result",
    "ExplainSources": "explain the result",
    "finish_answer": "the answer",
    "FinishAnswer": "the answer",
    "Clarify": "clarification",
}

# Word-boundary regex matching whole-token tool/action identifiers only.
# English lowercase "clarify" is intentionally absent from _PLAIN_WORDS.
_IDENTIFIER_PATTERN = re.compile(r"\b(" + "|".join(re.escape(k) for k in _PLAIN_WORDS) + r")\b")

CANNED_ACTION_THOUGHTS: frozenset[str] = frozenset(
    {
        "Next: look up the figures in your business data.\n",
        "Next: search the company documents.\n",
        "Next: explain the result you already have.\n",
        "Ready to write the answer.\n",
        "One detail is missing before this can be answered.\n",
    }
)


def replace_identifiers(text: str) -> str:
    """Replace internal tool/schema identifiers with plain words for panel display."""
    return _IDENTIFIER_PATTERN.sub(lambda m: _PLAIN_WORDS[m.group(0)], text)


class TurnThoughts:
    """Copy hygiene for the coordinator's thought stream.

    Replaces internal tool identifiers in provider reasoning summaries with
    plain words and puts a line break before a canned action line that
    follows streamed text. Which step hosts a thought, and when its phase
    closes, is the sink's decision from the step it has open.
    """

    def __init__(self, inner: CoordinatorProgress) -> None:
        self._inner = inner
        self._needs_newline = False

    def stage(self, stage: AskStage) -> None:
        self._inner.stage(stage)

    def emit(
        self,
        stage: ProgressStage,
        *,
        ordinal: int | None = None,
        of: int | None = None,
        subject: str | None = None,
    ) -> None:
        self._inner.emit(stage, ordinal=ordinal, of=of, subject=subject)

    def table(self, section: CommittedTable) -> None:
        self._inner.table(section)

    def emit_thought_delta(self, chunk: str) -> None:
        if not chunk:
            return
        cleaned = replace_identifiers(chunk)
        if cleaned in CANNED_ACTION_THOUGHTS and self._needs_newline:
            self._inner.emit_thought_delta("\n")
            self._needs_newline = False

        self._inner.emit_thought_delta(cleaned)
        if cleaned in CANNED_ACTION_THOUGHTS:
            self._needs_newline = False
        elif cleaned.endswith("\n"):
            self._needs_newline = False
        else:
            self._needs_newline = True

    def finish_thought(self) -> None:
        self._inner.finish_thought()
