"""Fence collision stripping and nonce-fenced JSON formatting."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping, Sequence

FENCE_COLLISION_STRIP_MAX_PASSES = 64


def strip_fence_collisions(text: str, pattern: re.Pattern[str]) -> str:
    """Remove matching fence marker substrings, looping to a fixpoint.

    Fixpoint iteration closes the reformation vulnerability where stripped
    overlapping markers leave adjacent substrings that form the marker again.
    """
    for _ in range(FENCE_COLLISION_STRIP_MAX_PASSES):
        stripped = pattern.sub("", text)
        if stripped == text:
            return stripped
        text = stripped
    return text


def fenced_json_block(
    *,
    marker: str,
    payload: Mapping[str, object] | Sequence[object],
    pattern: re.Pattern[str],
    nonce: str | None = None,
) -> str:
    """Serialize payload to compact JSON wrapped in nonce-delimited begin/end markers."""
    fence_nonce = nonce or secrets.token_hex(8)
    begin = f"BEGIN {marker} {fence_nonce}"
    end = f"END {marker} {fence_nonce}"
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if pattern.search(compact):
        compact = strip_fence_collisions(compact, pattern)
    return f"{begin}\n{compact}\n{end}"
