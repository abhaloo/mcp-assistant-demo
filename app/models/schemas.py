"""Pydantic request/response models for the Q&A API.

Decomposed into concept modules under app/models/; this module re-exports
the public HTTP symbols.
"""

from __future__ import annotations

from app.models.ask_request import (
    AskCancelRequest,
    AskOperation,
    Question,
)
from app.models.ask_response import (
    Answer,
    AskCancelResponse,
    FollowUpSuggestion,
    QueryType,
)
from app.models.citations import (
    CitationsPayload,
    CitedSourceRef,
    Source,
)
from app.models.client_directives import (
    ClientAction,
    DisambiguationCandidate,
    DisambiguationPayload,
)
from app.models.errors import ErrorResponse
from app.models.feedback import (
    Feedback,
    FeedbackReason,
    FeedbackResponse,
)
from app.models.sql_provenance import (
    QueryExplanation,
    RecordLink,
    SqlProvenance,
)
from app.models.timing import (
    QueryRecordTiming,
    QueryRecordTimingResponse,
)
from app.models.trusted_page_context import (
    PageRecord,
    TrustedPageContext,
)

__all__ = [
    "Answer",
    "AskCancelRequest",
    "AskCancelResponse",
    "AskOperation",
    "CitationsPayload",
    "CitedSourceRef",
    "ClientAction",
    "DisambiguationCandidate",
    "DisambiguationPayload",
    "ErrorResponse",
    "Feedback",
    "FeedbackReason",
    "FeedbackResponse",
    "FollowUpSuggestion",
    "PageRecord",
    "QueryExplanation",
    "QueryRecordTiming",
    "QueryRecordTimingResponse",
    "QueryType",
    "Question",
    "RecordLink",
    "Source",
    "SqlProvenance",
    "TrustedPageContext",
]
