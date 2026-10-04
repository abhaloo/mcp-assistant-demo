"""What a finished draft may not contain: evidence no succeeded action returned."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Generic, TypeVar

from app.conversation.coordinator.action_lifecycle import ActionOutcome
from app.conversation.coordinator.contracts import Clarify, FinishAnswer, Observation
from app.conversation.evidence.contracts import RestoredTurn
from app.conversation.followup_context import SelectedSources
from app.rag.retrieval.document_contracts import DocumentSearchResult

_ASSISTANT_VOICE = re.compile(r"\s*(you could|would you like|i can)\b", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\[[^\]]*\]")
_INSTRUCTION = re.compile(r"\s*(?:first\s+|then\s+)?ask\b", re.IGNORECASE)


def in_assistants_voice(text: str) -> bool:
    """True when the candidate starts in the assistant's voice."""
    return bool(_ASSISTANT_VOICE.match(text))


# A click posts the words as the person's question: a gap to fill or an order to
# the person is not a question they would type.
def not_a_next_question(text: str) -> bool:
    """True when the words cannot be sent as the person's next question."""
    return (
        in_assistants_voice(text)
        or _PLACEHOLDER.search(text) is not None
        or _INSTRUCTION.match(text) is not None
    )


def lacks_a_suggestion(draft: FinishAnswer, observations: Sequence[Observation]) -> bool:
    """True when a failed query has no suggestion the person could send."""
    if not any(obs.failure is not None for obs in observations):
        return False
    return not any(
        block.claim_type == "suggestion" and not not_a_next_question(block.text)
        for block in draft.blocks
    )


def stood_on_documents(turn: RestoredTurn) -> bool:
    """True when the restored answer stood on document passages, which a restore does
    not carry."""
    return "document_search" in turn.turn_result.selected or any(
        source.resource_type is None for source in turn.sources
    )


def restates_a_document_answer(draft: FinishAnswer, outcomes: Sequence[ActionOutcome]) -> bool:
    """True when the draft restates an earlier document answer with no passage citation.

    The draft cites a document-backed restored answer and cites no passage from a
    succeeded search_documents, whether or not a search ran.
    """
    cited = {eid for block in draft.blocks for eid in block.evidence_ids}
    restored = [o.result for o in outcomes if isinstance(o.result, SelectedSources)]
    document_ids = {
        turn.exchange_id
        for selected in restored
        for turn in selected.turns
        if stood_on_documents(turn)
    }
    search_passages = {
        p.id
        for o in outcomes
        if o.status == "succeeded" and isinstance(o.result, DocumentSearchResult)
        for p in o.result.passages
    }
    return bool(cited & document_ids) and not bool(cited & search_passages)


def names_a_record_type(text: str, record_types: Sequence[str]) -> bool:
    """Whether the text names one of these record types, as a whole word, singular or plural."""
    for record_type in record_types:
        label = re.escape(record_type.replace("_", " "))
        if re.search(rf"\b(?:{label}|{label}s|{label}es)\b", text, re.IGNORECASE):
            return True
    return False


def _cites_an_earlier_answer(allowed: frozenset[str]) -> str:
    """The repair for an earlier answer cited as evidence. It names explain_sources only
    while the turn still offers it."""
    if "explain_sources" in allowed:
        return (
            "it cites an earlier answer as evidence: restore it with explain_sources and "
            "cite what that returns, or write that sentence as a general block with no "
            "evidence ids"
        )
    return (
        "it cites an earlier answer that this turn did not restore, and no restore is "
        "left: write that sentence as a general block with no evidence ids"
    )


def draft_problems(
    draft: FinishAnswer,
    observations: Sequence[Observation],
    *,
    earlier: frozenset[str] = frozenset(),
    allowed: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Every reason the draft cannot publish, in the words the repair observation shows.

    Evidence ids must come from a succeeded action. ``allowed`` holds the actions the
    next decision offers; a repair never asks for an action outside it.
    """
    known = {eid for obs in observations for eid in obs.evidence_ids}
    invented = {eid for block in draft.blocks for eid in block.evidence_ids} - known
    problems: list[str] = []
    if invented & earlier:
        problems.append(_cites_an_earlier_answer(allowed))
    elif invented:
        problems.append("it cites evidence ids that no succeeded action returned")
    if _states_an_id_as_an_amount(draft, observations):
        problems.append("it states a record id as an amount; quote an amount the table shows")
    return tuple(problems)


# A currency code followed by a figure, for example "TZS 201,250,000".
_AMOUNT = re.compile(r"\b[A-Z]{3}\s?(\d[\d,]*(?:\.\d+)?)")


def _number(value: object) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def _cells(observations: Sequence[Observation], *, identifier: bool) -> set[Decimal]:
    """Every figure in the rows' identifier columns, or in their other columns."""
    keys = {c.key for obs in observations for c in obs.columns if c.identifier is identifier}
    return {
        number
        for obs in observations
        for row in obs.rows
        for key, value in row.items()
        if key in keys and (number := _number(value)) is not None
    }


def _states_an_id_as_an_amount(draft: FinishAnswer, observations: Sequence[Observation]) -> bool:
    ids = _cells(observations, identifier=True)
    amounts = _cells(observations, identifier=False)
    stated = {
        number
        for block in draft.blocks
        if block.claim_type == "evidence"
        for match in _AMOUNT.finditer(block.text)
        if (number := _number(match.group(1))) is not None
    }
    return any(number in ids and number not in amounts for number in stated)


ActionT = TypeVar("ActionT", FinishAnswer, Clarify)


@dataclass(frozen=True)
class DraftFacts:
    """What the rules read about the turn so far."""

    observations: Sequence[Observation]
    outcomes: Sequence[ActionOutcome]
    earlier: frozenset[str]
    allowed: frozenset[str]
    unreachable: Sequence[str]


@dataclass(frozen=True)
class DraftRule(Generic[ActionT]):
    """One rule a draft must keep. ``check`` returns the repair text, or None."""

    name: str
    check: Callable[[ActionT, DraftFacts], str | None]
    # The draft may still publish if the repair round fails.
    holds_draft: bool
    # A second break ends the turn; otherwise the second draft publishes.
    stops_when_repeated: bool


def _evidence(draft: FinishAnswer, facts: DraftFacts) -> str | None:
    problems = draft_problems(
        draft, facts.observations, earlier=facts.earlier, allowed=facts.allowed
    )
    return "; ".join(problems) + "." if problems else None


def _restated_document(draft: FinishAnswer, facts: DraftFacts) -> str | None:
    searched = any(o.kind == "search_documents" and o.status == "succeeded" for o in facts.outcomes)
    if not restates_a_document_answer(draft, facts.outcomes):
        return None
    # A restored answer carries no document source, so a document answer restated
    # from history streams uncited; if a search ran, cite its passages, otherwise
    # one more round searches again.
    if searched:
        return (
            "it restates a document answer without citing the search passages; "
            "cite the passages the search returned."
        )
    if "search_documents" in facts.allowed:
        return (
            "it restates a document answer without its source; "
            "search the documents and cite the passages."
        )
    return None


def _suggestion(draft: FinishAnswer, facts: DraftFacts) -> str | None:
    if not lacks_a_suggestion(draft, facts.observations):
        return None
    return "it offers no next question; add one to three suggestion blocks."


def asks_the_person(draft: FinishAnswer) -> bool:
    """True when no block carries evidence and the answer's own text ends in a question.

    Suggestion blocks are the person's next questions, not questions to them."""
    if any(block.evidence_ids for block in draft.blocks):
        return False
    text = " ".join(b.text for b in draft.blocks if b.claim_type != "suggestion").strip()
    return text.endswith("?")


def _asks_back(draft: FinishAnswer, facts: DraftFacts) -> str | None:
    if "clarify" not in facts.allowed or not asks_the_person(draft):
        return None
    return (
        "it asks the person a question inside an answer; use clarify with two to "
        "four choices, or answer without a question."
    )


def _choices(draft: Clarify, _facts: DraftFacts) -> str | None:
    if draft.choices:
        return None
    return "it offers no choices; give two to four short choices the person can pick."


def _unreachable_record_type(draft: Clarify, facts: DraftFacts) -> str | None:
    if not names_a_record_type(draft.question, facts.unreachable):
        return None
    return (
        "it asks about a record type this person cannot see; send the question to "
        "query_business and explain what is not available."
    )


FINISH_RULES: tuple[DraftRule[FinishAnswer], ...] = (
    DraftRule("evidence", _evidence, holds_draft=False, stops_when_repeated=True),
    DraftRule("asks_back", _asks_back, holds_draft=True, stops_when_repeated=False),
    DraftRule("restated_document", _restated_document, holds_draft=True, stops_when_repeated=False),
    DraftRule("suggestion", _suggestion, holds_draft=True, stops_when_repeated=False),
)

CLARIFY_RULES: tuple[DraftRule[Clarify], ...] = (
    DraftRule(
        "unreachable_record_type",
        _unreachable_record_type,
        holds_draft=False,
        stops_when_repeated=False,
    ),
    DraftRule("choices", _choices, holds_draft=True, stops_when_repeated=False),
)
