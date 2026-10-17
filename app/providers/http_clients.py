"""Process-lifetime httpx clients shared by every LLM/embedding model.

Process resources container (app.resources.ProcessResources) owns client lifecycle.
Callers pass ``resources=`` or rely on the lifespan/script bind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.resources import ProcessResources


def _owner(resources: ProcessResources | None) -> ProcessResources:
    from app.resources import current_process_resources

    return resources if resources is not None else current_process_resources()


def get_sync_http_client(resources: ProcessResources | None = None) -> httpx.Client:
    """Return the process-owned sync HTTP client (never an orphan)."""
    return _owner(resources).get_sync_http_client()


def get_async_http_client(resources: ProcessResources | None = None) -> httpx.AsyncClient:
    """Return the process-owned async HTTP client (never an orphan)."""
    return _owner(resources).get_async_http_client()


def get_document_sync_http_client(resources: ProcessResources | None = None) -> httpx.Client:
    """Return the short-timeout sync client reserved for document retrieval."""
    return _owner(resources).get_document_sync_http_client()


def get_document_async_http_client(
    resources: ProcessResources | None = None,
) -> httpx.AsyncClient:
    """Return the short-timeout async client reserved for document retrieval."""
    return _owner(resources).get_document_async_http_client()


def embedding_client_kwargs(
    resources: ProcessResources | None = None,
    *,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
) -> dict[str, object]:
    """HTTP client kwargs for an embeddings model.

    Without bounds: the process-wide clients and provider defaults. With a
    timeout or retry bound: the document clients plus those bounds.
    """
    if request_timeout_s is None and max_retries is None:
        return {
            "http_client": get_sync_http_client(resources),
            "http_async_client": get_async_http_client(resources),
        }
    kwargs: dict[str, object] = {
        "http_client": get_document_sync_http_client(resources),
        "http_async_client": get_document_async_http_client(resources),
    }
    if request_timeout_s is not None:
        kwargs["request_timeout"] = request_timeout_s
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return kwargs
