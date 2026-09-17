import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import get_resources
from app.auth.jwt import (
    Principal,
    enforce_record_context_binding,
    verify_principal_ask,
    verify_principal_cancel,
    verify_principal_feedback,
)
from app.core.ask_errors import (
    CAPABILITY_UNAVAILABLE,
    DOCUMENT_UNAVAILABLE,
    report_ask_failure,
)
from app.core.errors import (
    CapabilityUnavailableError,
    ContinuationClaimRejectedError,
    ContinuationRefRequiredError,
    DeadlineExceededError,
    DeadlineExpiredError,
    DocumentUnavailableError,
    GateCBlockedError,
    NotFoundError,
    RegenerateConflictError,
    ServiceUnavailableError,
)
from app.health.state import error_tracker
from app.models.ask_request import AskRequest
from app.models.schemas import (
    AskCancelRequest,
    AskCancelResponse,
    Feedback,
    FeedbackResponse,
    QueryRecordTiming,
    QueryRecordTimingResponse,
)
from app.rag.page_context import UnknownPageContextProfileError
from app.resources import ProcessResources
from app.services.ask_v2_service import AskV2Service
from app.services.ask_v2_turn_bound import new_expiry_event
from app.services.cancel_service import cancel_run
from app.services.feedback_service import FeedbackService
from app.services.query_record_timing_service import QueryRecordTimingService
from app.transport.ask_sse import stream_ask_events  # noqa: F401
from app.transport.sse import accepts_event_stream

router = APIRouter(tags=["Q&A"])
_ask_v2_service = AskV2Service()
_ask_service = _ask_v2_service
_feedback_service = FeedbackService()
_timing_service = QueryRecordTimingService()
logger = logging.getLogger(__name__)

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}
_GATE_C_BLOCKED = "gate_c_blocked"


def _ask_http_error(exc: BaseException, *, stage: str, role: str) -> JSONResponse:
    """Map typed Ask failures to HTTP for /ask and /ask/v2."""
    if isinstance(exc, UnknownPageContextProfileError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ContinuationRefRequiredError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, RegenerateConflictError):
        raise HTTPException(status_code=409, detail="regenerate_conflict") from exc
    if isinstance(exc, ContinuationClaimRejectedError):
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    if isinstance(exc, NotFoundError):
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    if isinstance(exc, DocumentUnavailableError):
        return JSONResponse(
            status_code=503, content={"error": DOCUMENT_UNAVAILABLE, "detail": exc.detail}
        )
    if isinstance(exc, CapabilityUnavailableError):
        return JSONResponse(
            status_code=503, content={"error": CAPABILITY_UNAVAILABLE, "detail": exc.detail}
        )
    if isinstance(exc, ServiceUnavailableError):
        raise HTTPException(status_code=503, detail=exc.detail) from exc
    error_tracker.record_500()
    payload = report_ask_failure(exc, stage=stage, role=role)
    raise HTTPException(status_code=500, detail=payload["detail"]) from exc


def _gate_c_blocked_response(exc: GateCBlockedError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": _GATE_C_BLOCKED,
            "detail": str(exc),
            "missing_inputs": list(exc.missing_inputs),
        },
    )


@router.post(
    "/ask",
    summary="Ask AI canonical endpoint",
)
@router.post(
    "/ask/v2",
    summary="Ask AI protocol version 2 endpoint (alias for /ask)",
)
async def ask_question(
    request: Request,
    body: AskRequest,
    principal: Principal = Depends(verify_principal_ask),
    _record_context_binding: None = Depends(enforce_record_context_binding),
    resources: ProcessResources = Depends(get_resources),
):
    server_now_ms = int(time.time() * 1000)
    try:
        deadline = _ask_v2_service.validate_deadline(
            body.deadline_at_ms, server_now_ms=server_now_ms
        )
    except DeadlineExpiredError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except DeadlineExceededError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    try:
        if accepts_event_stream(request.headers.get("accept")):
            # Checked here, before the StreamingResponse is constructed, so a
            # Gate C failure refuses with 503 before the response starts.
            # StreamingResponse begins sending as soon as it is returned;
            # once that happens, an exception raised inside the generator
            # body can no longer change the status code the client already
            # received. ask_v2_stream.py's own in-generator check is
            # defence in depth, not the real gate.
            expiry_event = new_expiry_event()
            gate_c = await _ask_v2_service.check_readiness(
                resources, turn_budget=deadline, expiry_event=expiry_event
            )
            if not gate_c.is_ready:
                raise GateCBlockedError(gate_c.missing_inputs)
            return StreamingResponse(
                _ask_v2_service.stream(
                    body,
                    principal,
                    resources=resources,
                    deadline=deadline,
                    disconnected=request.is_disconnected,
                    expiry_event=expiry_event,
                ),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )

        result = await _ask_v2_service.ask(
            body,
            principal,
            resources=resources,
            deadline=deadline,
        )
        return JSONResponse(status_code=200, content=jsonable_encoder(result))
    except DeadlineExpiredError as e:
        return JSONResponse(
            status_code=504,
            content={"error": "deadline_exceeded", "detail": str(e)},
        )
    except GateCBlockedError as e:
        return _gate_c_blocked_response(e)
    except HTTPException:
        raise
    except Exception as e:
        return _ask_http_error(e, stage="v2", role=principal.role)


# Backwards compatibility alias for python-level callers
ask_question_v2 = ask_question


@router.post(
    "/cancel",
    response_model=AskCancelResponse,
    summary="Cancel an in-flight streaming ask run",
)
async def cancel_ask(
    body: AskCancelRequest,
    principal: Principal = Depends(verify_principal_cancel),
):
    cancelled = await cancel_run(body.run_id, principal)
    return AskCancelResponse(cancelled=cancelled)


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="Submit a thumbs-up/down on a prior answer",
)
async def submit_feedback(
    body: Feedback,
    principal: Principal = Depends(verify_principal_feedback),
):
    try:
        return await _feedback_service.submit(body, principal)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail) from e


@router.post(
    "/query-record/timing",
    response_model=QueryRecordTimingResponse,
    summary="Record browser-measured UI timings on an existing Query Record row",
)
async def submit_query_record_timing(
    body: QueryRecordTiming,
    principal: Principal = Depends(verify_principal_ask),
):
    try:
        return await _timing_service.submit(body, principal)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.detail) from e
