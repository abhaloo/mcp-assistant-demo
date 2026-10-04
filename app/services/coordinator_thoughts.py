"""Turn thoughts adapter and identifier replacement for copy hygiene."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.auth import Principal
from app.business_query.authorize.capability import visible_members
from app.business_query.definitions import DefinitionBundle
from app.business_query.ports import ProgressStage
from app.business_query.wire.result_presentation import column_label
from app.conversation.coordinator.contracts import NOTE_MAX_CHARS
from app.rag.provenance.ui_links import resolve_page_slots
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

CANNED_ACTION_THOUGHTS: frozenset[str] = frozenset(
    {
        "Next: look up the figures in your business data.\n",
        "Next: search the company documents.\n",
        "Next: explain the result you already have.\n",
        "Ready to write the answer.\n",
        "One detail is missing before this can be answered.\n",
    }
)


# The coordinator's tool-schema words that are not plain English, each with the
# words the person reads. The list is closed; the test suite keeps it and
# PLAIN_ENGLISH_FIELDS complete and disjoint against the action schema.
SCHEMA_WORDS: dict[str, str] = {
    "claim_type": "claim type",
    "evidence_ids": "evidence ids",
    "note_to_person": "note",
    "source_positions": "source positions",
    "unanswered_part": "part not answered",
    "value_refs": "value references",
}
# Tool-schema property names and literals that are ordinary words. They stay as
# written: a rewrite of "question" or "value" would corrupt prose.
PLAIN_ENGLISH_FIELDS: frozenset[str] = frozenset(
    {
        "add",
        "binding",
        "blocks",
        "choices",
        "clarify",
        "compound",
        "continues",
        "evidence",
        "general",
        "kind",
        "member",
        "mentions",
        "op",
        "ops",
        "path",
        "question",
        "remove",
        "replace",
        "resource",
        "selection",
        "subject",
        "suggestion",
        "text",
        "value",
    }
)


# Words the coordinator's own prompt and observation text showed, or show, that are
# not plain English, each with the words the person reads. The suite keeps this list
# complete against the rendered prompt.
PROMPT_WORDS: dict[str, str] = {
    "anchor_count": "record count",
    "anchors_without_children": "records with nothing linked",
    "requested_anchor_count": "records asked for",
    "row_count": "row count",
    "row_identity": "record counts",
}
# An action id (act-1) names a step of this turn; the person reads the step.
ACTION_ID_PATTERN = re.compile(r"(?<!\w)act-(\d+)(?!\w)")


def plain_words(text: str, words: Mapping[str, str] | None = None) -> str:
    """Rewrite every listed word and action id in ``text`` into the words the person reads."""
    table = {**_PLAIN_WORDS, **SCHEMA_WORDS, **PROMPT_WORDS, **(words or {})}
    cleaned = _word_pattern(table).sub(lambda m: table[m.group(0)], text)
    return ACTION_ID_PATTERN.sub(r"step \1", cleaned)


_MARKDOWN_MARKERS = ("**", "__", "`")
_MARKDOWN_LINK_PATTERN = re.compile(r"\[.*?\]\(.*?\)|https?://")
_FIRST_PERSON_PATTERN = re.compile(r"(?i)\b(i|i['’](?:ll|m|ve|d)|me|my|let\s+me)\b")
_INTERNAL_WORDS_PATTERN = re.compile(
    r"(?i)\b(tools?|observations?|evidence(?:[\s_]+ids?)?|finaliz\w*|prompts?|schemas?|candidates?|json|sql)\b"
)
# A card member key (resource.field) with no space after the dot. The resource
# part has at least two letters, so "e.g." and "i.e." stay ordinary prose.
_MEMBER_KEY_PATTERN = re.compile(r"\b[a-z][a-z0-9_]+\.[a-z_][a-z0-9_]*\b")


def person_note(note: str | None, words: Mapping[str, str] | None = None) -> str | None:
    """Validate a coordinator decision's note_to_person.

    Returns the whitespace-folded note with a trailing newline if valid, or
    None if missing, too long, formatted with markdown, lacking first-person
    perspective, or containing internal words, listed terms, member keys, or
    action IDs.
    """
    if note is None or not isinstance(note, str):
        return None
    folded = " ".join(note.split())
    if (
        not folded
        or len(folded) > NOTE_MAX_CHARS
        or any(m in folded for m in _MARKDOWN_MARKERS)
        or folded.startswith("#")
        or _MARKDOWN_LINK_PATTERN.search(folded)
        or not _FIRST_PERSON_PATTERN.search(folded)
        or _INTERNAL_WORDS_PATTERN.search(folded)
        or ACTION_ID_PATTERN.search(folded)
        or _MEMBER_KEY_PATTERN.search(folded)
        or plain_words(folded, words) != folded
    ):
        return None
    return folded + "\n"


def member_words(principal: Principal, bundle: DefinitionBundle) -> dict[str, str]:
    """Each card member key the viewer can see, mapped to the words the panel shows.

    A title that only repeats the key (the bundle does this for dimensions) is
    dropped, so column_label builds the words from the key.
    """
    titles = {entry.name: entry.title for entry in bundle.capabilities}
    return {
        name: column_label(name, label=titles[name] if titles[name] != name else None)
        for name in visible_members(principal, bundle)
        if name in titles
    }


def _word_pattern(words: Mapping[str, str]) -> re.Pattern[str]:
    """Whole-token match over a closed word list, longest key first."""
    keys = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keys) + r")(?!\w)")


def _cut_before_open_slot(text: str, cut: int) -> int:
    """Move a whitespace cut back to an unclosed brace, so a page slot that
    continues in the next chunk is held whole instead of split across the cut."""
    open_at = text.rfind("{", 0, cut)
    while open_at != -1 and "}" in text[open_at:cut]:
        open_at = text.rfind("{", 0, open_at)
    if open_at == -1:
        return cut
    while open_at > 0 and text[open_at - 1] == "{":
        open_at -= 1
    return open_at


def _split_pending(text: str) -> tuple[str, str]:
    """The text safe to rewrite now, and the trailing token to hold back.

    A token cut at a chunk end may continue in the next chunk (invoice.items
    then _total), so only the text up to the last whitespace is rewritten now.
    An unclosed `{` may be a page slot that continues in the next chunk, so
    that tail is held until a `}` arrives or the thought ends. A whitespace cut
    that would land inside a brace group is moved back to the first brace, so
    the whole slot is held.
    """
    last_open = -1
    search_from = 0
    while True:
        open_at = text.find("{", search_from)
        if open_at == -1:
            break
        if "}" not in text[open_at:]:
            last_open = open_at
            break
        search_from = open_at + 1
    if last_open != -1:
        return text[:last_open], text[last_open:]
    if not text or text[-1].isspace():
        return text, ""
    cut = max(text.rfind(" "), text.rfind("\n")) + 1
    cut = _cut_before_open_slot(text, cut)
    return text[:cut], text[cut:]


class TurnThoughts:
    """Copy hygiene for the coordinator's thought stream.

    Rewrites whole tokens from four closed lists — tool identifiers, the
    coordinator's tool-schema words, its prompt words and the viewer's own card
    member keys — and every action id into plain words, rewrites page slots to
    their labels, holds back a token a chunk cut in two until the next chunk, a
    canned line, a step event or the end of the thought, and puts a line break
    before a canned action line that follows streamed text. Which step hosts a
    thought, and when its phase closes, is the sink's decision from the step it
    has open.
    """

    def __init__(
        self,
        inner: CoordinatorProgress,
        words: Mapping[str, str] | None = None,
        slot_labels: Mapping[str, str] | None = None,
    ) -> None:
        self._inner = inner
        self._needs_newline = False
        self._pending = ""
        self._words = {**_PLAIN_WORDS, **SCHEMA_WORDS, **PROMPT_WORDS, **(words or {})}
        self._pattern = _word_pattern(self._words)
        self._slot_labels = dict(slot_labels or {})

    def stage(self, stage: AskStage) -> None:
        self._flush_pending()
        self._inner.stage(stage)

    def emit(
        self,
        stage: ProgressStage,
        *,
        ordinal: int | None = None,
        of: int | None = None,
        subject: str | None = None,
    ) -> None:
        self._flush_pending()
        self._inner.emit(stage, ordinal=ordinal, of=of, subject=subject)

    def table(self, section: CommittedTable) -> None:
        self._flush_pending()
        self._inner.table(section)

    def emit_thought_delta(self, chunk: str) -> None:
        if not chunk:
            return
        if chunk in CANNED_ACTION_THOUGHTS:
            self._flush_pending()
            if self._needs_newline:
                self._inner.emit_thought_delta("\n")
            self._inner.emit_thought_delta(chunk)
            self._needs_newline = False
            return
        ready, self._pending = _split_pending(self._pending + chunk)
        if ready:
            self._send(ready)

    def finish_thought(self) -> None:
        self._flush_pending()
        self._inner.finish_thought()

    def _send(self, text: str) -> None:
        resolved = resolve_page_slots(text, (), self._slot_labels)
        cleaned = self._pattern.sub(lambda m: self._words[m.group(0)], resolved.text)
        cleaned = ACTION_ID_PATTERN.sub(r"step \1", cleaned)
        self._inner.emit_thought_delta(cleaned)
        self._needs_newline = not cleaned.endswith("\n")

    def _flush_pending(self) -> None:
        if self._pending:
            pending, self._pending = self._pending, ""
            self._send(pending)
