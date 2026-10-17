"""Which flags one message raises, and what the screen mode does with them (ADR 0087)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.guardrails.input_screen.questions import FLAG_QUESTIONS

ScreenMode = Literal["log", "block"]
ScreenAction = Literal["pass", "flag", "block", "unavailable"]

RULE_VERSION = "input-screen-r1-v1"
# Per question, the lowest grid value at which no benign design item was flagged.
THRESHOLDS: Mapping[str, float] = {
    "override": 0.3,
    "off_scope": 0.9,
    "authority_claim": 0.3,
    "internals": 0.3,
}
FIELD_SPOOF = "field_spoof"
# Flags that refuse the turn in block mode. An off-scope ask is only recorded: the answering
# prompt already redirects it. An authority claim is only recorded too (D6, 2026-10-06): staff
# naming their own job raised it, and role scope, not the screen, limits what a role reads.
BLOCKING_FLAGS = frozenset({"override", "internals", FIELD_SPOOF})

# The screen sends `message` inside a JSON state. A quote followed by a new key and a JSON
# value tries to end the message field and start another one. A pasted JSON snippet has the
# same shape, so the shape counts only where the message has no quoted string open.
_KEY_AFTER_QUOTE = re.compile(
    r'\\?"\s*[}\]]?\s*,\s*\\?"[A-Za-z_][A-Za-z0-9_]{0,40}\\?"\s*:\s*'
    r'(?:\\?"|true\b|false\b|null\b|-?\d|\{|\[)',
    re.IGNORECASE,
)
_JSON = json.JSONDecoder()
# A staff question never names one of the state's own keys as a field.
_STATE_KEY = re.compile(r"\b(?:person_role|assistant_scope)\b[\"']?\s*[:=]", re.IGNORECASE)


@dataclass(frozen=True)
class ScreenSignals:
    """Jev's answers for one message: a probability per flag question and the severity score."""

    nouls: Mapping[str, float]
    severity: float


@dataclass(frozen=True)
class ScreenDecision:
    """One message's result. `signals` is None when Jev gave no answer."""

    action: ScreenAction
    flags: tuple[str, ...]
    signals: ScreenSignals | None
    latency_ms: int
    request_id: str | None = None
    unavailable_reason: str | None = None

    @property
    def blocks(self) -> bool:
        return self.action == "block"


def _without_json_snippets(message: str) -> str:
    """The message with every complete JSON object or array replaced by `0`, so that a
    pasted snippet cannot change which quotes are open, while a key whose value is such a
    snippet still reads as a key with a value."""
    kept: list[str] = []
    position = 0
    while position < len(message):
        if message[position] in "{[":
            try:
                _, end = _JSON.raw_decode(message, position)
            except ValueError:
                pass
            else:
                kept.append("0")
                position = end
                continue
        kept.append(message[position])
        position += 1
    return "".join(kept)


def _string_open_at(text: str, end: int) -> bool:
    """Whether a quoted string is open just before `end`. A quote right after a digit, when
    no string is open, is an inch mark (24"), not a quote."""
    inside = False
    for index in range(end):
        if text[index] != '"':
            continue
        if not inside and index > 0 and text[index - 1].isdigit():
            continue
        inside = not inside
    return inside


def is_field_spoof(message: str) -> bool:
    if _STATE_KEY.search(message):
        return True
    text = _without_json_snippets(message)
    return any(
        not _string_open_at(text, match.start()) for match in _KEY_AFTER_QUOTE.finditer(text)
    )


def raised_flags(signals: ScreenSignals | None, message: str) -> tuple[str, ...]:
    """Flag questions at or over their threshold, then the field-spoof rule. The rule is code,
    so it still runs when Jev gave no answer."""
    flags = (
        [] if signals is None else [q for q in FLAG_QUESTIONS if signals.nouls[q] >= THRESHOLDS[q]]
    )
    if is_field_spoof(message):
        flags.append(FIELD_SPOOF)
    return tuple(flags)


def action_for(mode: ScreenMode, flags: tuple[str, ...], *, answered: bool) -> ScreenAction:
    if mode == "block" and BLOCKING_FLAGS.intersection(flags):
        return "block"
    if flags:
        return "flag"
    return "pass" if answered else "unavailable"
