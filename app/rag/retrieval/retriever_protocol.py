"""
Vector store provider abstraction — two Protocols, by design.

PYTHON CONCEPT: Protocol (structural typing). Any class with matching
methods satisfies the Protocol; no explicit `implements` needed.

Why two Protocols instead of one?
Interface Segregation. The query path (app/rag/chains/document_chain.py) only needs to
build a retriever. The ingest path (scripts/deploy/ingest.py) only needs to
add or clear documents. Splitting keeps each caller dependent on the
smallest surface it actually uses. A concrete class — e.g.
ChromaVectorStore — can satisfy both Protocols at once.
"""

from typing import Final, Protocol

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

# Spell privilege out. None still means "no filter" at the implementation level,
# but no signature defaults to it anymore — the most privileged state must be
# the most deliberate one (ADR 0014).
UNRESTRICTED: Final = None
AccessTiers = list[str] | None


class Retriever(Protocol):
    """
    Read path. Returns a LangChain BaseRetriever for LCEL chains.

    access_tiers contract (three states — implementations MUST honour):
        None     → no filter applied. Admin / unrestricted view.
        []       → empty allowed-set. Caller has zero permissions.
                   Implementations MUST return a retriever that yields
                   zero documents — never fall through to "no filter".
        [...]    → tier-restricted. Only documents whose access_tier is
                   in the list may be returned.
    """

    def as_retriever(self, k: int, access_tiers: AccessTiers) -> BaseRetriever: ...


class Indexer(Protocol):
    """Write path. Embed-and-store, plus wipe-the-collection."""

    def add_documents(self, documents: list[Document]) -> None: ...

    def clear(self) -> None: ...


class EmptyRetriever(BaseRetriever):
    """
    A BaseRetriever that returns zero documents for any query.

    Used by concrete vector stores when access_tiers=[] — the caller has
    no permissions and must not see any document. Short-circuiting in
    Python is safer than building an empty backend filter and trusting
    Chroma/Azure to interpret it as "match nothing".
    """

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        return []


def batched_add(
    store: object,
    documents: list[Document],
    *,
    batch_size: int = 50,
) -> None:
    """Embed and store document chunks in batches."""
    import logging

    logger = logging.getLogger(__name__)

    for i in range(0, len(documents), batch_size):
        batch = documents[i : i + batch_size]
        ids = [d.metadata["chunk_id"] for d in batch if "chunk_id" in d.metadata]
        kwargs = {"ids": ids} if len(ids) == len(batch) else {}
        getattr(store, "add_documents")(batch, **kwargs)
        logger.info("Ingested batch %d (%d chunks)", i // batch_size + 1, len(batch))
