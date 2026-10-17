"""Extraction and redaction of conversation history for the input screen (Plan D4, D4b)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.conversation.transcript_models import TranscriptTurn
from app.query_records.content import redact_question

logger = logging.getLogger(__name__)

REDACTED = "<redacted>"


def screen_conversation(history: Sequence[TranscriptTurn]) -> list[dict[str, str]]:
    """Build the conversation history list for the input screen.

    Per Plan D4b: user content is included verbatim; assistant content is passed
    through redact_question. If redaction fails for any reason, the turn is replaced
    with REDACTED and a warning is logged without leaking message text.
    """
    result: list[dict[str, str]] = []
    for turn in history:
        if turn.role == "assistant":
            try:
                content = redact_question(turn.content)
            except Exception:  # noqa: BLE001 — fail open: drop the text, never fail the turn
                logger.warning("input screen: assistant message redaction failed")
                content = REDACTED
        else:
            content = turn.content
        result.append({"role": turn.role, "content": content})
    return result
