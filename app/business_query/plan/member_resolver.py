"""Map a person's phrase to one bundle member the principal may read.

Scoring order:
1. Exact match on declared label or entry title (score 3).
2. All phrase tokens present in entry name, label, or title tokens (score 2).
3. All phrase tokens present in entry description tokens (score 1).

Equal top scores create an ambiguous result unless broken by preferred resource.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.auth import Principal
from app.business_query.authorize.capability import visible_entries
from app.business_query.definitions.schema import CapabilityEntry, DefinitionBundle

_TOKEN = re.compile(r"[a-z0-9]+")


class MemberMatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    member: str
    kind: Literal["dimension", "measure"]
    score: int


class Ambiguous(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    candidates: tuple[MemberMatch, ...]


class NotFound(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    phrase: str


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.casefold()))


def _normalize(text: str) -> str:
    return " ".join(_TOKEN.findall(text.casefold()))


def _is_exact_match(raw_phrase: str, norm_phrase: str, label_text: str, title: str) -> bool:
    exact_targets = {
        label_text.casefold().strip(),
        title.casefold().strip(),
        _normalize(label_text),
        _normalize(title),
    }
    exact_targets.discard("")
    return bool(raw_phrase in exact_targets or norm_phrase in exact_targets)


def _matches_name_or_title(wanted: set[str], entry: CapabilityEntry, label_text: str) -> bool:
    sources = (entry.name, label_text, entry.title)
    return any(wanted <= _tokens(s) for s in sources if s)


def _score_entry(
    entry: CapabilityEntry,
    wanted: set[str],
    raw_phrase: str,
    norm_phrase: str,
    label_text: str,
) -> int:
    if _is_exact_match(raw_phrase, norm_phrase, label_text, entry.title):
        return 3
    if _matches_name_or_title(wanted, entry, label_text):
        return 2
    if wanted <= _tokens(entry.description):
        return 1
    return 0


def _break_tie(best: list[MemberMatch], prefer_resource: str | None) -> list[MemberMatch]:
    if not prefer_resource:
        return best
    preferred = [m for m in best if m.member.split(".", 1)[0] == prefer_resource]
    if preferred:
        return preferred
    return best


def resolve_member_phrase(
    phrase: str,
    bundle: DefinitionBundle,
    principal: Principal,
    *,
    prefer_resource: str | None = None,
) -> MemberMatch | Ambiguous | NotFound:
    """Resolve a natural phrase to one visible bundle member."""
    wanted = _tokens(phrase)
    if not wanted:
        return NotFound(phrase=phrase)

    labels = {d.name: d.label for d in bundle.dimensions}
    norm_phrase = _normalize(phrase)
    raw_phrase = phrase.casefold().strip()

    scored: list[MemberMatch] = []
    for entry in visible_entries(principal, bundle):
        if entry.kind not in {"dimension", "measure"}:
            continue

        label_text = labels.get(entry.resolves_to) or ""
        score = _score_entry(entry, wanted, raw_phrase, norm_phrase, label_text)
        if score:
            scored.append(MemberMatch(member=entry.name, kind=entry.kind, score=score))

    if not scored:
        return NotFound(phrase=phrase)

    top = max(m.score for m in scored)
    best = [m for m in scored if m.score == top]

    if len(best) > 1:
        best = _break_tie(best, prefer_resource)

    if len(best) > 1:
        return Ambiguous(candidates=tuple(sorted(best, key=lambda m: m.member)))
    return best[0]
