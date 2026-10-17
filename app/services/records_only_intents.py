"""Deterministic intent classification for questions over visible page records."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.records_only_types import RecordsIntent, ResponseLanguage

_COUNT_QUESTION_RE = re.compile(r"\b(?:how many|number of|count|ngapi|idadi ya)\b", re.IGNORECASE)
_FIELD_OR_ITEM_COUNT_RE = re.compile(
    r"\b(?:pages|page\s+count|number\s+of\s+pages?|no[._\s]+pages?|"
    r"kurasa|pieces?|qty|quantit(?:y|ies)|items?|units?|ordered)\b",
    re.IGNORECASE,
)
_HIGH_PRIORITY_RE = re.compile(
    r"\b(?:high[ -]?priority|priority\s+(?:is\s+)?high|kipaumbele\s+cha\s+juu)\b",
    re.IGNORECASE,
)
_NORMAL_PRIORITY_RE = re.compile(
    r"\b(?:normal[ -]?priority|priority\s+(?:is\s+)?normal|"
    r"kipaumbele\s+cha\s+kawaida)\b",
    re.IGNORECASE,
)
_PAGE_OVERVIEW_RE = re.compile(
    r"\b(?:summari[sz]e|summary|each job|jobs? (?:currently )?shown|"
    r"jobs? (?:on )?this page|muhtasari|orodhesha|kazi.*ukurasa)\b",
    re.IGNORECASE,
)
_CUSTOMERS_ON_PAGE_RE = re.compile(
    r"\bcustomers?\b.*\b(?:shown|here|page|which|list)\b|"
    r"\b(?:which|list)\b.*\bcustomers?\b|"
    r"\bwateja\b.*\b(?:ukurasa|gani|orodhesha)\b|"
    r"\b(?:gani|orodhesha)\b.*\bwateja\b",
    re.IGNORECASE,
)
_TOP_PRIORITY_RE = re.compile(
    r"\b(?:highest|top|most urgent)\s+(?:priority\s+)?(?:job|work order)\b|"
    r"\b(?:job|work order)\b.*\b(?:highest|top)\s+priority\b|"
    r"\bkazi\s+(?:gani|ipi).*\bkipaumbele\s+cha\s+juu\s+zaidi\b|"
    r"\bkazi\s+yenye\s+kipaumbele\s+cha\s+juu\s+zaidi\b",
    re.IGNORECASE,
)
_SECONDARY_RANKING_RE = re.compile(
    r"\b(?:earliest|latest|oldest|newest|delivery|due|created|updated|status|"
    r"customer|department|mapema|mwisho|tarehe|hali|mteja|idara)\b",
    re.IGNORECASE,
)
_GROUP_BY_CUSTOMER_RE = re.compile(
    r"\b(?:group(?:ed|ing)? by|by) customer\b|\bkwa mteja\b",
    re.IGNORECASE,
)
_SWAHILI_MARKERS_RE = re.compile(
    r"\b(?:ngapi|idadi|nionyeshe|orodhesha|muhtasari|ukurasa|wateja|"
    r"kipaumbele|zilizo|yenye|gani|ipi|kazi)\b",
    re.IGNORECASE,
)
_STATUS_CLAUSE_RE = re.compile(
    r"\b(?:in[ -]?progress|open|finished|completed|pending|cancelled|canceled|"
    r"hali|inaendelea|imekamilika)\b",
    re.IGNORECASE,
)
_EXPLANATION_CLAUSE_RE = re.compile(
    r"\b(?:why|because|explain|reason|kwa nini|sababu)\b",
    re.IGNORECASE,
)
_SELECTION_CLAUSE_RE = re.compile(
    r"\b(?:which\s+(?:single\s+)?(?:job|one)|need\s+attention\s+first|"
    r"prioriti[sz]e)\b",
    re.IGNORECASE,
)
_ADDITIONAL_CLAUSE_RE = re.compile(r"(?:,\s*)?\b(?:and|or)\b\s+", re.IGNORECASE)


@dataclass(frozen=True)
class RecordsIntentMatch:
    """A deterministic intent plus the material clauses it can safely answer."""

    intent: RecordsIntent | None
    material_clauses: tuple[str, ...]
    consumed_clauses: tuple[str, ...]

    @property
    def uncovered_clauses(self) -> tuple[str, ...]:
        return tuple(
            clause for clause in self.material_clauses if clause not in self.consumed_clauses
        )

    @property
    def coverage_percentage(self) -> int:
        if not self.material_clauses:
            return 0
        return round(100 * len(self.consumed_clauses) / len(self.material_clauses))

    @property
    def is_complete(self) -> bool:
        return self.intent is not None and not self.uncovered_clauses


def response_language(question: str) -> ResponseLanguage:
    return "sw" if _SWAHILI_MARKERS_RE.search(question) else "en"


def _classify_base_intent(question: str) -> RecordsIntent | None:
    language = response_language(question)
    if _TOP_PRIORITY_RE.search(question) and not _SECONDARY_RANKING_RE.search(question):
        return RecordsIntent(kind="top_priority", language=language)

    if _COUNT_QUESTION_RE.search(question) and not _FIELD_OR_ITEM_COUNT_RE.search(question):
        priority_filter = None
        if _HIGH_PRIORITY_RE.search(question):
            priority_filter = "high"
        elif _NORMAL_PRIORITY_RE.search(question):
            priority_filter = "normal"
        return RecordsIntent(
            kind="count",
            language=language,
            priority_filter=priority_filter,
        )

    if _CUSTOMERS_ON_PAGE_RE.search(question):
        return RecordsIntent(kind="customers", language=language)

    if _PAGE_OVERVIEW_RE.search(question):
        return RecordsIntent(
            kind="overview",
            language=language,
            group_by_customer=bool(_GROUP_BY_CUSTOMER_RE.search(question)),
        )

    return None


def classify_records_intent(question: str) -> RecordsIntentMatch:
    """Classify only when every material clause has deterministic support.

    The immediate coverage gate intentionally recognizes only unsupported modifiers
    that can change a page-record answer. Unknown language remains conservative:
    it falls through to the existing fenced model path rather than being treated
    as consumed.
    """
    intent = _classify_base_intent(question)
    material: list[str] = []
    consumed: list[str] = []

    if intent is not None:
        material.append("intent")
        consumed.append("intent")

    priority_requested = bool(
        _HIGH_PRIORITY_RE.search(question) or _NORMAL_PRIORITY_RE.search(question)
    )
    if priority_requested:
        material.append("priority")
        if intent is not None and intent.kind in {"count", "top_priority"}:
            consumed.append("priority")

    if _STATUS_CLAUSE_RE.search(question):
        material.append("status")

    if _FIELD_OR_ITEM_COUNT_RE.search(question):
        material.append("field_count")

    if _EXPLANATION_CLAUSE_RE.search(question):
        material.append("explanation")

    if _SELECTION_CLAUSE_RE.search(question):
        material.append("selection")
        if intent is not None and intent.kind == "top_priority":
            consumed.append("selection")

    if _ADDITIONAL_CLAUSE_RE.search(question):
        material.append("additional_clause")

    return RecordsIntentMatch(
        intent=intent,
        material_clauses=tuple(material),
        consumed_clauses=tuple(consumed),
    )
