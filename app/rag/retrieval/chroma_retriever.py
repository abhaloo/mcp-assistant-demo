"""
ChromaDB implementation of the Retriever + Indexer Protocols.

All Chroma-specific code lives here. Callers (invoke_rag_turn_with_usage,
scripts/deploy/ingest.py) go through the factory and see only the Protocol surface —
they never import langchain_chroma or know what a `search_kwargs` dict looks like.
"""

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.config import settings
from app.providers import get_embeddings
from app.rag.retrieval.retriever_protocol import AccessTiers, EmptyRetriever


class ChromaVectorStore:
    """Satisfies both Retriever and Indexer Protocols."""

    def __init__(
        self,
        collection_name: str | None = None,
        *,
        request_timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        # Cache the underlying Chroma handle. Constructing it opens the
        # persist directory and loads the collection index, so reuse it
        # across calls instead of rebuilding on every retrieve.
        self._store = Chroma(
            collection_name=collection_name or settings.collection_name,
            embedding_function=get_embeddings(
                request_timeout_s=request_timeout_s, max_retries=max_retries
            ),
            persist_directory=settings.chroma_persist_dir,
        )

    # ---- Retriever Protocol -------------------------------------------------

    def as_retriever(self, k: int, access_tiers: AccessTiers) -> BaseRetriever:
        """Build a LangChain BaseRetriever with Chroma's metadata filter.

        See `Retriever` Protocol for the three-state access_tiers contract.
        """
        if access_tiers is None:
            # Admin / no filter applied.
            search_kwargs: dict = {"k": k}
        elif not access_tiers:
            # Caller has zero permissions — short-circuit before hitting
            # the backend. Building an empty $in filter and trusting
            # Chroma to interpret it as "match nothing" would be the
            # silent-leak failure mode we're guarding against.
            return EmptyRetriever()
        else:
            # Chroma's $in operator on metadata field. Azure AI Search
            # translates the same access_tiers list into OData syntax.
            search_kwargs = {"k": k, "filter": {"access_tier": {"$in": access_tiers}}}

        return self._store.as_retriever(
            search_type="similarity",
            search_kwargs=search_kwargs,
        )

    # ---- Indexer Protocol ---------------------------------------------------

    def add_documents(self, documents: list[Document]) -> None:
        """Embed and store document chunks in batches."""
        from app.rag.retrieval.retriever_protocol import batched_add

        batched_add(self._store, documents)

    def clear(self) -> None:
        """Wipe the collection. Used before a fresh ingest."""
        self._store.reset_collection()

    def ping(self) -> None:
        """Cheap connectivity probe for health checks — raises if unreachable.

        Encapsulates the one langchain-internal access so health-check code
        never reaches through `_store` itself.
        """
        self._store._collection.count()  # noqa: SLF001 — connectivity only
