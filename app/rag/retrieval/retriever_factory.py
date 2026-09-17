"""
Vector store factory — single dispatch point for retriever + indexer.

Mirrors app/providers/factory.py. Callers depend on the Protocols
(app/rag/retrieval/retriever_protocol.py), never on a concrete implementation.
"""

from functools import cache

from langchain_core.retrievers import BaseRetriever

from app.config import settings
from app.core.process_state import register_resettable
from app.rag.document_rag_gate import (
    require_document_rag_enabled as _require_document_rag_enabled,
)
from app.rag.retrieval.retriever_protocol import AccessTiers, Indexer, Retriever

DOCUMENT_REQUEST_TIMEOUT_S = 2.0
DOCUMENT_MAX_RETRIES = 0


def _build_store(
    collection_name: str | None,
    *,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
):
    _require_document_rag_enabled()
    kind = settings.retriever_kind
    if kind == "chroma":
        from app.rag.retrieval.chroma_retriever import ChromaVectorStore

        return ChromaVectorStore(
            collection_name=collection_name,
            request_timeout_s=request_timeout_s,
            max_retries=max_retries,
        )
    if kind == "azure_search":
        from app.rag.retrieval.azure_search_retriever import AzureSearchVectorStore

        return AzureSearchVectorStore(
            index_name=collection_name,
            request_timeout_s=request_timeout_s,
            max_retries=max_retries,
        )
    raise ValueError(f"Unknown retriever_kind: {kind!r}. Expected 'chroma' or 'azure_search'.")


@cache
def _get_store(collection_name: str | None = None):
    """Cache the concrete store per collection across calls."""
    return _build_store(collection_name)


@cache
def _get_document_store(collection_name: str | None = None):
    """Cache the DocumentExecutor store: 2s remote requests, zero retries."""
    return _build_store(
        collection_name,
        request_timeout_s=DOCUMENT_REQUEST_TIMEOUT_S,
        max_retries=DOCUMENT_MAX_RETRIES,
    )


def get_retriever(collection_name: str | None = None) -> Retriever:
    """Return the active retriever (read path)."""
    return _get_store(collection_name)


def get_document_retriever(collection_name: str | None = None) -> Retriever:
    """Return the bounded retriever DocumentExecutor searches with."""
    return _get_document_store(collection_name)


def get_indexer(collection_name: str | None = None) -> Indexer:
    """Return the active indexer (write path)."""
    return _get_store(collection_name)


def build_retriever(
    k: int,
    access_tiers: AccessTiers,
    collection_name: str | None = None,
    *,
    store: Retriever | None = None,
) -> BaseRetriever:
    """Build the active retriever, optionally wrapped with rerank-as-filter."""
    candidate_k = settings.rerank_candidates if settings.rerank_enabled else k
    vector_store = store if store is not None else get_retriever(collection_name)
    base = vector_store.as_retriever(k=candidate_k, access_tiers=access_tiers)
    if not settings.rerank_enabled or settings.retriever_kind != "chroma":
        return base
    from flashrank import Ranker

    from app.rag.retrieval.reranker import RerankingRetriever

    ranker = Ranker(
        model_name=settings.rerank_model,
        cache_dir=str(settings.rerank_cache_dir),
    )
    return RerankingRetriever(
        base_retriever=base,
        top_k=k,
        score_floor=settings.rerank_score_floor,
        model_name=settings.rerank_model,
        ranker=ranker,
    )


register_resettable(_get_store.cache_clear)
register_resettable(_get_document_store.cache_clear)
