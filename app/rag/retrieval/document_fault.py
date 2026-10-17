"""Default-off document fault seam for controlled live failure drills."""

from __future__ import annotations

import logging

from app.rag.retrieval.document_policy import DocumentFault

logger = logging.getLogger(__name__)


def apply_document_fault(mode: DocumentFault) -> None:
    """Raise a stage timeout when the armed fault value is ``stage_timeout``."""
    if mode == "none":
        return
    logger.info("document_fault_armed", extra={"ask_document_fault": mode})
    if mode == "stage_timeout":
        raise TimeoutError("document fault seam")
