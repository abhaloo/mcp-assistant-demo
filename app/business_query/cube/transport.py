"""HTTP transport to a Cube Core service.

One call is one Cube REST `load`: the principal's facts travel as a signed JWT,
the query as JSON. Cube long-polls: a body of {"error": "Continue wait"} means
the same query is still running and must be asked again, under the same span
id, until it answers or this transport's deadline ends."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

_CONTINUE_WAIT = "Continue wait"
_TOKEN_TTL_SECONDS = 60
# One HTTP exchange. Cube answers `Continue wait` after its configured hold
# (deploy/cube/cube.py), which must end before this read timeout does.
CONNECT_TIMEOUT_SECONDS = 2.0
READ_TIMEOUT_SECONDS = 5.0


class CubeTransportError(RuntimeError):
    """The service did not answer the query; the adapter reports Incomplete."""


class HttpCubeTransport:
    def __init__(
        self,
        url: str,
        api_secret: str,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.25,
        client: httpx.Client | None = None,
    ) -> None:
        self._load_url = f"{url.rstrip('/')}/load"
        self._secret = api_secret
        self._deadline_seconds = timeout_seconds
        self._poll = poll_interval_seconds
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        )

    def __repr__(self) -> str:
        return f"HttpCubeTransport(url={self._load_url!r})"

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = jwt.encode(
            {**payload["securityContext"], "exp": int(time.time()) + _TOKEN_TTL_SECONDS},
            self._secret,
            algorithm="HS256",
        )
        span = uuid.uuid4().hex
        body = {"query": payload["query"]}
        deadline = time.monotonic() + self._deadline_seconds
        polls = 0
        while True:
            headers = {"Authorization": token, "x-request-id": f"{span}-span-{polls + 1}"}
            try:
                response = self._client.post(self._load_url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise CubeTransportError(f"cube request failed: {type(exc).__name__}") from exc
            if response.is_error:
                raise CubeTransportError(f"cube answered {response.status_code}")
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise CubeTransportError("cube answered a non-object body")
            if decoded.get("error") != _CONTINUE_WAIT:
                if "error" in decoded:
                    raise CubeTransportError(f"cube error: {str(decoded['error'])[:120]}")
                logger.info("cube load answered span=%s polls=%s", span, polls)
                return decoded
            polls += 1
            if time.monotonic() >= deadline:
                raise CubeTransportError(
                    f"cube query timed out after {self._deadline_seconds}s ({polls} polls)"
                )
            time.sleep(self._poll)
