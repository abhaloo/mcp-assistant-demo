"""Token matching and string normalization for value resolution."""

from __future__ import annotations

import re
import unicodedata

_MAX_LOOKUP_TOKENS = 12
_TOKEN_BOUNDARY = re.compile(r"(?<=\d)(?=\D)|(?<=\D)(?=\d)")


def normalize_lookup_value(raw: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", raw).split())


def lookup_tokens(raw: str) -> tuple[str, ...]:
    """Split a lookup value into the parts a stored name must carry."""
    tokens: list[str] = []
    for word in normalize_lookup_value(raw).casefold().split():
        tokens.extend(part for part in _TOKEN_BOUNDARY.split(word) if part)
    return tuple(dict.fromkeys(tokens))[:_MAX_LOOKUP_TOKENS]


def token_match(candidate: str, tokens: tuple[str, ...]) -> bool:
    """True when `candidate` carries every token, in order, numbers intact."""
    candidate_tokens = lookup_tokens(candidate)
    position = 0
    for token in tokens:
        numeric = token.isdigit()
        for offset in range(position, len(candidate_tokens)):
            other = candidate_tokens[offset]
            if other == token or (not numeric and not other.isdigit() and token in other):
                position = offset + 1
                break
        else:
            return False
    return True
