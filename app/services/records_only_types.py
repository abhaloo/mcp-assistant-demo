"""Contracts shared by records-only intent, presentation, and orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.models.schemas import RecordLink

ResponseLanguage = Literal["en", "sw"]
RecordsIntentKind = Literal["top_priority", "count", "customers", "overview"]
PriorityFilter = Literal["high", "normal"]


@dataclass(frozen=True)
class RecordsIntent:
    kind: RecordsIntentKind
    language: ResponseLanguage
    priority_filter: PriorityFilter | None = None
    group_by_customer: bool = False


@dataclass(frozen=True)
class RecordsOnlyResult:
    answer: str
    cited_record_ids: list[str]
    record_links: list[RecordLink]
    follow_up_suggestions: list[str] = field(default_factory=list)
