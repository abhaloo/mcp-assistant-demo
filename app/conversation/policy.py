"""Bounds for conversation transcript size (per-turn, prompt, storage)."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.conversation.transcript_models import TURN_CONTENT_MAX, TranscriptTurn


@dataclass(frozen=True)
class HistoryPolicy:
    """Single place for the three history bounds (per-turn, prompt, storage)."""

    max_turn_chars: int
    max_prompt_chars: int
    max_stored_turns: int

    @classmethod
    def current(cls) -> HistoryPolicy:
        return cls(
            max_turn_chars=TURN_CONTENT_MAX,
            max_prompt_chars=settings.conversation_history_max_chars,
            max_stored_turns=settings.conversation_max_turns,
        )

    def cap_for_prompt(self, turns: list[TranscriptTurn]) -> list[TranscriptTurn]:
        """Drop oldest turns until assembled history text fits the prompt char cap."""
        if self.max_prompt_chars <= 0 or not turns:
            return turns
        trimmed = list(turns)
        while trimmed and sum(len(t.content) for t in trimmed) > self.max_prompt_chars:
            trimmed.pop(0)
        return trimmed

    def trim_for_storage(self, turns: list[TranscriptTurn]) -> list[TranscriptTurn]:
        return turns[-self.max_stored_turns :] if self.max_stored_turns > 0 else turns
