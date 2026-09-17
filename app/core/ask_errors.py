"""User-safe ask error mapping + Sentry reporting."""

from __future__ import annotations

import logging

from app.core.errors import (
    CAPABILITY_UNAVAILABLE_MESSAGE as CAPABILITY_UNAVAILABLE_MESSAGE,
)
from app.core.errors import (
    CapabilityUnavailableError,
    CircuitOpenError,
    ConversationStoreUnavailableError,
    DocumentUnavailableError,
    ModelRouteUnavailableError,
    ServiceUnavailableError,
    TranscriptStoreContentionError,
)
from app.providers.model_purpose import ModelPurpose
from app.providers.model_registry import PolicyViolationError
from app.providers.route_policy import ResolvedModelRoute, RouteContext, RouteResolutionError

logger = logging.getLogger(__name__)

# Stable codes for dashboards / Sentry tags — not shown raw to end users.
AZURE_AUTH = "azure_auth"
CONVERSATION_STORE = "conversation_store"
SERVICE_BUSY = "service_unavailable"
CAPABILITY_UNAVAILABLE = "capability_unavailable"
DOCUMENT_UNAVAILABLE = "document_unavailable"
INTERNAL = "internal_error"

_ERROR_CODES: dict[type[Exception], str] = {
    TranscriptStoreContentionError: CONVERSATION_STORE,
    ConversationStoreUnavailableError: CONVERSATION_STORE,
    CircuitOpenError: SERVICE_BUSY,
    ModelRouteUnavailableError: SERVICE_BUSY,
    RouteResolutionError: SERVICE_BUSY,
    PolicyViolationError: SERVICE_BUSY,
}


def _typed_code(exc: Exception) -> str | None:
    """First matching base class wins, so a subclass inherits its parent's code."""
    for cls in type(exc).__mro__:
        code = _ERROR_CODES.get(cls)
        if code is not None:
            return code
    return None


DOCUMENT_UNAVAILABLE_MESSAGE = (
    "Document search is temporarily offline in this deployment. "
    "Showing authorized business records only."
)

INCOMPLETE_ANSWER_MESSAGE = (
    "I ran out of room to finish that database lookup. "
    "Try a narrower question, or ask again with fewer steps."
)

# A resolved answer that could not be sealed for the record. Distinct from
# INCOMPLETE_ANSWER_MESSAGE: the query itself finished, but the answer cannot
# ship without its evidence, so it is withheld rather than shown unrecorded.
EVIDENCE_UNAVAILABLE_MESSAGE = "I couldn't record this answer, so I'm not showing it."

# The honest failure when the record-dispatch gate is OPEN but the bounded
# tool loop resolves zero rows -- distinct from CAPABILITY_UNAVAILABLE_MESSAGE
# (gate closed / capability doesn't exist at all). Never a guess, never a
# silent semantic fallback: forbidden == missing (no count/type/id oracle --
# the same fixed literal regardless of resource type, filters tried, or how
# many tool calls ran).
#
# A zero-row loop result is not reliably distinguishable from the question
# being inexpressible with the current manifest (e.g. "biggest invoices this
# month" has no amount/date field to filter on -- RecordAccessDenied
# swallows to the same empty state as a genuine zero-hit search. This wording
# asserts nothing about existence in
# either direction, and never promises a capability that may not exist.
NO_MATCHING_RECORDS_MESSAGE = "I wasn't able to answer that from the records I can look up."


def resolve_production_route(
    purpose: ModelPurpose,
    context: RouteContext | None = None,
) -> ResolvedModelRoute:
    """Resolve a production route or raise Ask's existing 503 contract.

    Fail-closed: no implicit mini fallback when a route is missing or denied.
    """
    from app.providers.route_policy import get_route_policy

    try:
        return get_route_policy().resolve(purpose, context)
    except (RouteResolutionError, PolicyViolationError) as exc:
        raise ModelRouteUnavailableError(f"model route unavailable: {exc}") from exc


def rethrow_model_route_denial(exc: BaseException) -> None:
    """Map route/policy denials from downstream LLM construction to 503."""
    if isinstance(exc, (RouteResolutionError, PolicyViolationError)):
        raise ModelRouteUnavailableError(f"model route unavailable: {exc}") from exc
    raise exc


def is_model_route_denial(exc: BaseException) -> bool:
    """True when a failure is a route/policy gap, not transient SQL infra."""
    return isinstance(exc, (RouteResolutionError, PolicyViolationError, ModelRouteUnavailableError))


def _is_azure_auth_failure(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {"ClientAuthenticationError", "CredentialUnavailableError"}:
        return True
    msg = str(exc)
    return "DefaultAzureCredential" in msg or "EnvironmentCredential" in msg


def map_ask_failure(exc: BaseException) -> dict[str, str]:
    """Return SSE/HTTP-safe {error, detail} for a failed ask step."""
    if isinstance(exc, DocumentUnavailableError):
        return {"error": DOCUMENT_UNAVAILABLE, "detail": exc.detail or DOCUMENT_UNAVAILABLE_MESSAGE}

    if isinstance(exc, CapabilityUnavailableError):
        return {"error": CAPABILITY_UNAVAILABLE, "detail": CAPABILITY_UNAVAILABLE_MESSAGE}

    if isinstance(exc, (RouteResolutionError, PolicyViolationError)):
        # A resolve-time route/capability denial (app.providers.route_policy /
        # model_registry) maps straight to the busy contract -- never the 500
        # fallback, and never a route id or capability name in the response.
        return {
            "error": SERVICE_BUSY,
            "detail": "AI search is temporarily unavailable. Try again shortly.",
        }

    if isinstance(exc, ServiceUnavailableError):
        code = _typed_code(exc)
        if code == CONVERSATION_STORE:
            return {
                "error": CONVERSATION_STORE,
                "detail": (
                    "Chat history is temporarily unavailable. "
                    "Your answer may not remember prior turns."
                ),
            }
        if code == SERVICE_BUSY:
            return {
                "error": SERVICE_BUSY,
                "detail": "Search is busy right now. Wait a moment and try again.",
            }

        detail = exc.detail or "AI search is temporarily unavailable. Try again shortly."
        return {"error": SERVICE_BUSY, "detail": detail}

    if _is_azure_auth_failure(exc):
        return {
            "error": AZURE_AUTH,
            "detail": (
                "AI search couldn't sign in to Azure. "
                "If you're on local Docker, add Azure credentials to your .env and restart the API."
            ),
        }

    return {
        "error": INTERNAL,
        "detail": "Something went wrong answering your question. The issue has been logged.",
    }


def report_ask_failure(
    exc: BaseException,
    *,
    stage: str,
    role: str,
) -> dict[str, str]:
    """Log + optional Sentry, then return the public error payload."""
    payload = map_ask_failure(exc)
    logger.error(
        "ask failed stage=%s code=%s role=%s",
        stage,
        payload["error"],
        role,
        exc_info=exc,
    )
    _capture_sentry(exc, stage=stage, role=role, error_code=payload["error"])
    return payload


def _capture_sentry(
    exc: BaseException,
    *,
    stage: str,
    role: str,
    error_code: str,
) -> None:
    try:
        from app.config import settings

        if not settings.sentry_dsn:
            return
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("ask_stage", stage)
            scope.set_tag("ask_error_code", error_code)
            scope.set_tag("role", role)
            scope.set_level("error")
            sentry_sdk.capture_exception(exc)
    except Exception:
        logger.debug("sentry capture skipped", exc_info=True)
