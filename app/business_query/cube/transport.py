"""HTTP transport to a Cube Core service.

One call is one Cube REST `load`: the principal's facts travel as a signed JWT,
the query as JSON. Cube long-polls: a body of {"error": "Continue wait"} means
the same query is still running and must be asked again, under the same span
id, until it answers or this transport's deadline ends."""

from __future__ import annotations

import inspect
import logging
import threading
import time
import uuid
from collections.abc import Callable
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


_HTTP_SERVER_ERROR = 500


def accepts_cancel_token(call: Callable[..., Any]) -> bool:
    """True when ``call`` takes a ``cancel_token`` keyword, by name or through ``**kwargs``."""
    parameters = inspect.signature(call).parameters
    return "cancel_token" in parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()
    )


class CubeTransportError(RuntimeError):
    """The service did not answer the query. Subclasses say why."""


class CubeCancelled(CubeTransportError):
    """The turn was cancelled while the service was still working on the query."""


class CubeTimeout(CubeTransportError, TimeoutError):
    """The poll loop reached its own deadline while the service kept answering Continue wait."""


class CubeUnavailable(CubeTransportError):
    """The service could not be reached or answered with a server error."""


class CubeBadResponse(CubeTransportError):
    """The service answered, but not with a query result."""


class HttpCubeTransport:
    def __init__(  # noqa: PLR0913
        self,
        url: str,
        api_secret: str,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.25,
        client: httpx.Client | None = None,
        cancel_token: threading.Event | None = None,
    ) -> None:
        self._load_url = url.rstrip("/") + "/load"
        self._cancel_url = f"{url.rstrip('/')}/running-query/"
        self._secret = api_secret
        self._deadline_seconds = timeout_seconds
        self._poll = poll_interval_seconds
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
        )
        self._cancel_token = cancel_token or threading.Event()

    def __repr__(self) -> str:
        return f"HttpCubeTransport(url={self._load_url!r})"

    def _post_and_decode(self, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        try:
            response = self._client.post(self._load_url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise CubeUnavailable(f"cube exchange timed out: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            raise CubeUnavailable(f"cube request failed: {type(exc).__name__}") from exc
        if response.status_code >= _HTTP_SERVER_ERROR:
            raise CubeUnavailable(f"cube answered {response.status_code}")
        if response.is_error:
            raise CubeBadResponse(f"cube answered {response.status_code}")
        try:
            decoded = response.json()
        except ValueError as exc:
            raise CubeBadResponse("cube answered a non-JSON body") from exc
        if not isinstance(decoded, dict):
            raise CubeBadResponse("cube answered a non-object body")
        return decoded

    def _cancel_upstream(self, span: str, token: str) -> None:
        """Best effort, bounded, never raised: the person already stopped."""
        try:
            self._client.delete(
                self._cancel_url + span,
                headers={"Authorization": token},
                timeout=1.0,
            )
        except httpx.HTTPError as exc:
            logger.info(
                "cube cancel not delivered",
                extra={"span": span, "error": type(exc).__name__},
            )

    def __call__(
        self, payload: dict[str, Any], *, cancel_token: threading.Event | None = None
    ) -> dict[str, Any]:
        token_to_use = cancel_token if cancel_token is not None else self._cancel_token
        security_context = payload.get("securityContext", {})
        token = jwt.encode(
            {**security_context, "exp": int(time.time()) + _TOKEN_TTL_SECONDS},
            self._secret,
            algorithm="HS256",
        )
        span = uuid.uuid4().hex
        body = {"query": payload.get("query", {})}
        deadline = time.monotonic() + self._deadline_seconds
        polls = 0
        while True:
            if token_to_use.is_set():
                self._cancel_upstream(span, token)
                raise CubeCancelled(f"cancelled after {polls} polls")
            headers = {"Authorization": token, "x-request-id": f"{span}-span-{polls + 1}"}
            decoded = self._post_and_decode(body, headers)
            if decoded.get("error") != _CONTINUE_WAIT:
                if "error" in decoded:
                    raise CubeBadResponse(f"cube error: {str(decoded['error'])[:120]}")
                logger.info("cube load answered span=%s polls=%s", span, polls)
                return decoded
            polls += 1
            if time.monotonic() >= deadline:
                raise CubeTimeout(
                    f"cube query timed out after {self._deadline_seconds}s ({polls} polls)"
                )
            time.sleep(self._poll)
