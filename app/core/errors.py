"""Shared exceptions for service and conversation layers."""


class ServiceUnavailableError(Exception):
    """Raised when a dependency is unreachable (mapped to HTTP 503)."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class TranscriptStoreContentionError(ServiceUnavailableError):
    """Redis optimistic-lock retries exhausted on transcript append."""


class ConversationStoreUnavailableError(ServiceUnavailableError):
    """Conversation transcript storage or retrieval failed."""


class CircuitOpenError(ServiceUnavailableError):
    """A dependency's breaker is open, so the call was refused before it was made."""


class ModelRouteUnavailableError(ServiceUnavailableError):
    """A required model route cannot be resolved or violates policy."""


class CapabilityUnavailableError(ServiceUnavailableError):
    """A permanent capability gap (e.g. Phase-0 SQL containment denial) —

    distinct from ServiceUnavailableError's genuine "retry later" meaning
    (task A9). Subclasses ServiceUnavailableError on purpose so any existing
    ``except ServiceUnavailableError`` / ``pytest.raises(ServiceUnavailableError)``
    call site still catches it unless it explicitly special-cases this type
    first — but callers that DO care (app/api/router.py, ask_stream.py's SSE
    emit site) must check for this subclass before falling through to the
    generic "busy, try again" contract, since retrying can never help here.
    """


class ResultPageDisabledError(CapabilityUnavailableError):
    """The result_page operation is switched off for this deployment."""


class DocumentUnavailableError(ServiceUnavailableError):
    """Document RAG is disabled in this deployment."""


class RegenerateConflictError(Exception):
    """Latest exchange replacement preconditions failed — map to HTTP 409."""


class NotFoundError(Exception):
    """The addressed resource does not exist — map to HTTP 404."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class DeadlineExpiredError(Exception):
    """Client deadline has already passed beyond permitted clock skew."""


class DeadlineExceededError(Exception):
    """Client deadline exceeds maximum allowed future timeout."""


class GateCBlockedError(Exception):
    """Gate C production readiness precondition check failed."""

    def __init__(self, missing_inputs: tuple[str, ...]) -> None:
        self.missing_inputs = missing_inputs
        super().__init__(
            f"Ask AI v2 route unavailable (Gate C blocked: {', '.join(missing_inputs)})"
        )


class QueueBufferExceededError(Exception):
    """Ask AI stream queue exceeded maximum byte or item buffer limit."""


class KeyringConfigurationError(Exception):
    """Invalid BUSINESS_QUERY_EVENT_ENCRYPTION_KEYS configuration."""


class ContinuationRefRequiredError(Exception):
    """A clarification_reply operation was submitted without continuation_ref."""


class ContinuationClaimRejectedError(Exception):
    """A continuation claim was rejected by the conversation store."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class ContinuationUnavailableError(ServiceUnavailableError):
    """A same-key join did not observe a completed result before the join timeout."""


CAPABILITY_UNAVAILABLE_MESSAGE = (
    "I can't answer questions that need live business data yet — I can help "
    "with documents, policies, and records you open from Global Search."
)
