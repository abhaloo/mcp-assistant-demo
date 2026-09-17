"""Fixed replies for bare greetings and thanks; anything longer goes to the coordinator."""

from __future__ import annotations

import re

GREETING_REPLY_EN = (
    "Hello! Ask me a question about your business data, or ask what I can help with."
)
GREETING_REPLY_SW = (
    "Habari! Niulize swali kuhusu data ya biashara yako, au uliza ninachoweza kusaidia."
)
THANKS_REPLY_EN = "You're welcome. Ask another question whenever you are ready."
THANKS_REPLY_SW = "Karibu. Uliza swali lingine ukiwa tayari."

_GREETINGS_EN = frozenset({"hello", "hi", "hey", "good morning", "good afternoon", "good evening"})
_GREETINGS_SW = frozenset({"habari", "mambo", "shikamoo", "hujambo", "salama"})
_THANKS_EN = frozenset({"thanks", "thank you", "cheers"})
_THANKS_SW = frozenset({"asante", "asante sana"})
_PUNCTUATION = re.compile(r"[^\w\s]+")


def _normalize(question: str) -> str:
    return " ".join(_PUNCTUATION.sub(" ", question.lower()).split())


def greeting_reply(question: str) -> str | None:
    phrase = _normalize(question)
    if phrase in _THANKS_SW:
        return THANKS_REPLY_SW
    if phrase in _THANKS_EN:
        return THANKS_REPLY_EN
    if phrase in _GREETINGS_SW:
        return GREETING_REPLY_SW
    if phrase in _GREETINGS_EN:
        return GREETING_REPLY_EN
    return None
