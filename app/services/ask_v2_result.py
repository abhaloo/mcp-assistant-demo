"""The typed result each v2 operation hands the stream: an answer or a clarification card."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import ContinuationUnavailableError
from app.models.ask_response import Answer
from app.models.client_directives import DisambiguationPayload


class ClarificationCard(BaseModel):
    """The card a turn ends on when it needs the person to choose before it can answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    continuation_ref: str | None
    continuation: str | None
    prompt: str
    choices: list[dict[str, Any]]
    allow_free_text: bool = True
    disambiguation: DisambiguationPayload | None = None


V2Result = Answer | ClarificationCard


_CARD_OUTCOME = "clarification_required"


def stored_result(result: V2Result) -> dict[str, Any]:
    """The JSON-safe form the continuation store keeps; a card says it is one."""
    if isinstance(result, ClarificationCard):
        return {"outcome": _CARD_OUTCOME, **result.model_dump(mode="json")}
    return result.model_dump(mode="json")


def replayed_result(raw: object) -> V2Result:
    """The stored result a replayed or joined continuation returns; unavailable if invalid.

    The business query outcome is a strict model, so the stored dump is validated as
    JSON text: a date or decimal the dump wrote as a string validates the way it was
    written.
    """
    if not isinstance(raw, Mapping):
        raise ContinuationUnavailableError("continuation_unavailable")
    fields = dict(raw)
    try:
        if fields.pop("outcome", None) == _CARD_OUTCOME:
            return ClarificationCard.model_validate_json(json.dumps(fields))
        return Answer.model_validate_json(json.dumps(fields))
    except (ValidationError, TypeError) as exc:
        raise ContinuationUnavailableError("continuation_unavailable") from exc


def is_clarification(result: V2Result) -> bool:
    """True when the turn ends on a card: the card itself, or an answer whose query asked back."""
    if isinstance(result, ClarificationCard):
        return True
    wire = result.business_query
    return wire is not None and wire.outcome == "clarification_required"


@dataclass(frozen=True)
class V2Reply:
    """What one v2 operation returns: its result, and the digest its evidence barrier sealed.

    The digest is None when no barrier ran yet; the stream then runs its own.
    """

    result: V2Result
    evidence_digest: str | None

    def json_body(self) -> dict[str, Any]:
        """The v2 JSON route's body: the result's fields plus the digest."""
        if isinstance(self.result, ClarificationCard):
            body = {"outcome": "clarification_required", **self.result.model_dump()}
        else:
            body = {**self.result.model_dump(), "turn_result": self.result.turn_result}
        return {**body, "evidence_digest": self.evidence_digest}
