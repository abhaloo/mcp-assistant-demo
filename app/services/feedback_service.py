"""Feedback submission orchestration — token verification and durable storage."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.auth import Principal
from app.core.errors import NotFoundError
from app.models.schemas import Feedback, FeedbackResponse
from app.query_records.content import subject_digest as compute_subject_digest
from app.query_records.late_writes import persist_feedback
from app.services.feedback_tokens import verify_feedback_token
from app.telemetry import run_in_thread
from app.telemetry.langsmith_capture import record_feedback

logger = logging.getLogger(__name__)


class FeedbackService:
    async def submit(self, body: Feedback, principal: Principal) -> FeedbackResponse:
        if not verify_feedback_token(
            body.feedback_token,
            trace_id=body.trace_id,
            principal=principal,
        ):
            raise NotFoundError("feedback not authorized for this trace")

        source_info = {
            "user_id": str(principal.user_id),
            "role": principal.role,
        }
        recorded = await run_in_thread(
            record_feedback,
            body.trace_id,
            verdict=body.verdict,
            comment=body.comment,
            reason=body.reason,
            source_info=source_info,
        )
        durable = await persist_feedback(
            correlation_id=body.trace_id,
            feedback_verdict=body.verdict,
            feedback_at=datetime.now(tz=UTC),
            subject_digest=compute_subject_digest(str(principal.user_id)),
        )
        if not recorded and not durable:
            logger.info(
                "feedback dropped (tracing off or LangSmith error) trace_id=%s", body.trace_id
            )
        return FeedbackResponse(recorded=recorded or durable)
