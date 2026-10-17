"""Page and action links: Billing-declared destinations, offered only when
the viewer may use them."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.auth.principal import Principal
from app.business_query.definitions.schema import DefinitionBundle, UiDestination
from app.models.ui_link import UiLink
from app.rag.provenance.record_links import grants_satisfied
from app.rag.provenance.safe_path import is_safe_same_origin_path

# An opener is tolerant on purpose: the coordinator may echo the slot with one
# brace, or with spaces around `page`. Only spaces and tabs separate the parts,
# so an opener never spans a line break.
_SLOT_OPEN_RE = re.compile(r"\{\{?[ \t]*page[ \t]*:[ \t]*", re.IGNORECASE)
# A key is dot, underscore or dash separated segments. A sentence period after
# the key is not part of the key, so `...index.` keeps its period.
_KEY_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
# A second `page:` opener echoed inside a resolved key's slot.
_PAGE_HINT_RE = re.compile(r"page[ \t]*:", re.IGNORECASE)
OFFER_CAP = 12


def permitted_destinations(
    bundle: DefinitionBundle, principal: Principal
) -> tuple[UiDestination, ...]:
    return tuple(
        d
        for d in bundle.ui_destinations
        if is_safe_same_origin_path(d.href)
        and grants_satisfied(d.link_permissions, d.link_permission_mode, principal)
    )


def _first_mention(dest: UiDestination, text: str) -> int | None:
    """Earliest word-bounded, case-insensitive mention of the label or an alias."""
    hits = [
        m.start()
        for name in (dest.label, *dest.aliases)
        if (m := re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE))
    ]
    return min(hits) if hits else None


def offered_for_text(permitted: Sequence[UiDestination], text: str) -> tuple[UiDestination, ...]:
    """Permitted destinations the text mentions, ranked by first mention, then capped."""
    ranked = sorted(
        ((pos, d) for d in permitted if (pos := _first_mention(d, text)) is not None),
        key=lambda pair: (pair[0], pair[1].key),
    )
    return tuple(d for _, d in ranked[:OFFER_CAP])


@dataclass(frozen=True)
class SlotResolution:
    """One pass over user-visible text: cleaned text, links, and slot counts.

    ``linked`` counts offered slots, ``label_only`` counts known but unoffered
    slots, ``removed`` counts well-formed slots whose key is unknown, and
    ``guard_stripped`` counts malformed slots cleaned.
    """

    text: str
    links: tuple[UiLink, ...]
    linked: int
    label_only: int
    removed: int
    guard_stripped: int


def _known_slot_end(text: str, key_end: int) -> int:
    """Index after a resolved key's slot: through a closer on the same line that
    no later brace group owns, else through the key.

    Text after a bare key is kept, so an unclosed slot does not delete the words
    that follow it. Junk between the key and the closer — punctuation, or a
    second ``page:`` opener echoed inside the slot — goes with the slot, so no
    raw key or brace reaches the reader."""
    line_end = text.find("\n", key_end)
    if line_end == -1:
        line_end = len(text)
    closer = text.find("}", key_end)
    if closer == -1 or closer >= line_end or text.find("{", key_end, closer) != -1:
        return key_end
    between = text[key_end:closer]
    if any(ch in " \t" for ch in between.strip(" \t")) and not _PAGE_HINT_RE.search(between):
        return key_end
    end = closer + 1
    if end < len(text) and text[end] == "}":
        end += 1
    return end


def _has_closer_after_key(text: str, key_end: int) -> bool:
    """True when only spaces separate the key from a closing brace on its line."""
    i = key_end
    while i < len(text) and text[i] in " \t":
        i += 1
    return i < len(text) and text[i] == "}"


def _malformed_slot_end(text: str, opener_start: int, opener_end: int, key_end: int) -> int:
    """Index after a malformed slot: through its closer on the opener's line, or
    through the key when that line carries no closer, or when a later brace
    group starts before the closer (that closer belongs to the later group)."""
    line_end = text.find("\n", opener_start)
    if line_end == -1:
        line_end = len(text)
    closer = text.find("}", opener_end)
    if closer != -1 and closer < line_end:
        if text.find("{", opener_end, closer) != -1:
            return key_end
        end = closer + 1
        if end < len(text) and text[end] == "}":
            end += 1
        return end
    return key_end


def resolve_page_slots(
    text: str, offered: Sequence[UiDestination], labels: Mapping[str, str]
) -> SlotResolution:
    """Rewrite every page slot in one pass and clean malformed ones.

    A slot whose key is known becomes its label, plus a link when the page is
    offered. A missing closing brace does not stop the rewrite and does not
    delete the text that follows. A malformed slot — unknown key, no key, a key
    with a space, or no closer — is removed through its closer on the same line,
    or through its opener and key when that line has no closer. Text outside a
    removed slot, on a later line or in a later brace group, stands untouched.
    """
    by_key = {d.key: d for d in offered}
    links: dict[str, UiLink] = {}
    pieces: list[str] = []
    linked = label_only = removed = guard_stripped = 0
    cursor = 0
    for opener in _SLOT_OPEN_RE.finditer(text):
        start, after_open = opener.start(), opener.end()
        if start < cursor:
            continue
        key_match = _KEY_RE.match(text, after_open)
        key = key_match.group(0).lower() if key_match else ""
        key_end = key_match.end() if key_match else after_open
        dest = by_key.get(key) if key else None
        pieces.append(text[cursor:start])
        if dest is not None:
            pieces.append(dest.label)
            links.setdefault(
                dest.key, UiLink(key=dest.key, kind=dest.kind, label=dest.label, href=dest.href)
            )
            linked += 1
            cursor = _known_slot_end(text, key_end)
        elif key and key in labels:
            pieces.append(labels[key])
            label_only += 1
            cursor = _known_slot_end(text, key_end)
        else:
            if key_match is not None and _has_closer_after_key(text, key_end):
                removed += 1
            else:
                guard_stripped += 1
            cursor = _malformed_slot_end(text, start, after_open, key_end)
    pieces.append(text[cursor:])
    return SlotResolution(
        text="".join(pieces),
        links=tuple(links.values()),
        linked=linked,
        label_only=label_only,
        removed=removed,
        guard_stripped=guard_stripped,
    )
