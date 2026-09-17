"""Document RAG enablement gate."""

from __future__ import annotations

import app.config


def require_document_rag_enabled() -> None:
    """Raise RuntimeError when Document RAG is disabled."""
    if not app.config.settings.document_rag_enabled:
        raise RuntimeError("Document RAG is disabled in this deployment")
