"""The TypeSafe Jev call for the input screen. The only product module that imports
typesafe-sdk; SDK errors and response shapes become `JevUnavailable` and `JevAnswer` here."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import typesafe_sdk
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.guardrails.input_screen.decision import ScreenSignals
from app.guardrails.input_screen.questions import FLAG_QUESTIONS, INPUT_QUESTIONS, SEVERITY

JEV_MODEL = "jev-1.13.0"
JEV_PROVIDER = "typesafe"
# The screen fails open, so a retry only adds latency to the turn.
REQUEST_TIMEOUT_S = 1.0
CALL_DEADLINE_S = 1.5

JevClient = typesafe_sdk.AsyncTypeSafeClient
UnavailableReason = Literal[
    "timeout", "connection", "rate_limited", "bad_response", "api_error", "auth"
]


class JevUnavailable(Exception):
    """Jev gave no usable answer. The screen records the reason and lets the turn proceed."""

    def __init__(self, reason: UnavailableReason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason: UnavailableReason = reason
        self.detail = detail


@dataclass(frozen=True)
class JevAnswer:
    signals: ScreenSignals
    answers: dict[str, Any]
    input_tokens: int | None
    output_tokens: int | None
    request_id: str | None


class _Wire(BaseModel):
    # Unknown fields are ignored so a later API version can add fields.
    model_config = ConfigDict(extra="ignore")


class _WireUsage(_Wire):
    input_tokens: int | None = None
    output_tokens: int | None = None


class _WireNoul(_Wire):
    type: Literal["noul"]
    noul: float = Field(ge=0.0, le=1.0)


class _WireScore(_Wire):
    type: Literal["score"]
    score: float


class _WireBody(_Wire):
    model: str
    usage: _WireUsage
    answers: dict[str, Annotated[_WireNoul | _WireScore, Field(discriminator="type")]]


def build_jev_client(api_key: str) -> JevClient:
    return typesafe_sdk.AsyncTypeSafeClient(
        api_key=api_key,
        model=JEV_MODEL,
        retry=typesafe_sdk.RetryPolicy(max_retries=0, timeout=REQUEST_TIMEOUT_S),
        timeout=REQUEST_TIMEOUT_S,
    )


async def close_jev_client(client: JevClient) -> None:
    await client.aclose()


def parse_answer(body: Mapping[str, Any], request_id: str | None) -> JevAnswer:
    """Signals from a SystemOneResponse body; every flag question and severity must be there."""
    try:
        wire = _WireBody.model_validate(dict(body))
    except ValidationError as exc:
        raise JevUnavailable("bad_response", f"{exc.error_count()} shape errors") from exc
    if wire.model != JEV_MODEL:
        raise JevUnavailable("bad_response", f"answered by {wire.model}")
    nouls = {q: a.noul for q, a in wire.answers.items() if isinstance(a, _WireNoul)}
    severity = wire.answers.get(SEVERITY)
    missing = sorted(set(FLAG_QUESTIONS) - set(nouls))
    if missing or not isinstance(severity, _WireScore):
        raise JevUnavailable("bad_response", f"missing answers: {missing or [SEVERITY]}")
    return JevAnswer(
        signals=ScreenSignals(nouls={q: nouls[q] for q in FLAG_QUESTIONS}, severity=severity.score),
        answers=dict(body["answers"]),
        input_tokens=wire.usage.input_tokens,
        output_tokens=wire.usage.output_tokens,
        request_id=request_id,
    )


async def ask_jev(client: JevClient, state: Mapping[str, Any]) -> JevAnswer:
    """One call with a hard deadline. Subclasses are caught before their parents; the SDK's
    own timeout error is a `TimeoutError`, like the deadline's."""
    try:
        async with asyncio.timeout(CALL_DEADLINE_S):
            response = await client.system_one(dict(state), INPUT_QUESTIONS, model=JEV_MODEL)
    except TimeoutError as exc:
        raise JevUnavailable("timeout", type(exc).__name__) from exc
    except (
        typesafe_sdk.TypeSafeAuthenticationError,
        typesafe_sdk.TypeSafePermissionDeniedError,
    ) as exc:
        raise JevUnavailable("auth", type(exc).__name__) from exc
    except typesafe_sdk.TypeSafeAPIConnectionError as exc:
        raise JevUnavailable("connection", type(exc).__name__) from exc
    except typesafe_sdk.TypeSafeRateLimitError as exc:
        raise JevUnavailable("rate_limited", f"status {exc.status}") from exc
    except typesafe_sdk.TypeSafeAPIResponseValidationError as exc:
        raise JevUnavailable("bad_response", f"field {exc.field_path}") from exc
    except typesafe_sdk.TypeSafeAPIError as exc:
        raise JevUnavailable("api_error", f"status {exc.status}") from exc
    try:
        request_id = response.request_id
    except typesafe_sdk.TypeSafeError:
        # The SDK raises when the x-typesafe-request-id header is absent.
        request_id = None
    return parse_answer(response.model_dump(mode="json"), request_id)
