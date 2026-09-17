"""HTTP orchestration for Query Record late writes (S1b)."""

from __future__ import annotations

from app.auth import Principal
from app.core.errors import NotFoundError
from app.models.schemas import QueryRecordTiming, QueryRecordTimingResponse
from app.query_records.content import subject_digest as compute_subject_digest
from app.query_records.late_writes import persist_client_timings
from app.services.feedback_tokens import verify_feedback_token


class QueryRecordTimingService:
    async def submit(
        self, body: QueryRecordTiming, principal: Principal
    ) -> QueryRecordTimingResponse:
        if not verify_feedback_token(
            body.feedback_token,
            trace_id=body.trace_id,
            principal=principal,
        ):
            raise NotFoundError("timing not authorized for this trace")

        ui_first_text_ms = (
            body.ui_first_text_ms
            if body.ui_first_text_ms is not None
            else (
                body.ui_first_activity_ms
                if body.ui_first_activity_ms is not None
                else (body.ui_first_progress_ms if body.ui_first_progress_ms is not None else 0)
            )
        )
        completion_latency_ms = (
            body.completion_latency_ms
            if body.completion_latency_ms is not None
            else (body.ui_completion_latency_ms if body.ui_completion_latency_ms is not None else 0)
        )

        stored = await persist_client_timings(
            correlation_id=body.trace_id,
            ui_first_text_ms=ui_first_text_ms,
            completion_latency_ms=completion_latency_ms,
            subject_digest=compute_subject_digest(str(principal.user_id)),
        )
        return QueryRecordTimingResponse(stored=stored)
