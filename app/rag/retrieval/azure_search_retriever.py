"""
Azure AI Search implementation of the Retriever + Indexer Protocols.

Same Protocols as ChromaVectorStore (RetrieverProtocol). The
factory picks one or the other based on settings.retriever_kind. Callers
(invoke_rag_turn_with_usage, scripts/deploy/ingest.py) never import this module directly.

KEY DIFFERENCES vs ChromaVectorStore — worth internalizing:

1. The schema is explicit.
   Every metadata field that needs filtering must be declared
   filterable=True in the index schema. Forget that and the filter silently
   no-ops — i.e. a basic user gets finance docs. Chroma has no equivalent
   trap; metadata is opaque dict.

2. Filters are OData strings, not dicts.
   {"access_tier": {"$in": ["sales", "finance"]}}   ← Chroma
   "search.in(access_tier, 'sales,finance', ',')"   ← Azure AI Search
   Translation lives in _build_filter() below.

3. Indexing requires the index to exist.
   The langchain wrapper auto-creates from the `fields` schema on first
   add — but only if the index doesn't exist. Schema changes require
   dropping + recreating the index (see `clear()` below). New filterable
   fields take effect on the next blue-green build (ADR 0017): Azure allows
   adding fields to an existing index, but changing field types or
   filterable flags requires a fresh index — never mutate the live one.

4. Embedding dimensions are locked in the schema.
   vector_search_dimensions=1536 matches text-embedding-3-small. If you
   ever swap to a different-dimension embedding model, you MUST drop the
   index and reingest. Chroma fails loudly on add; Azure fails at query.

5. Read and write use DIFFERENT clients, split by privilege (least-privilege).
   The WRITE path (add_documents / clear) uses the langchain AzureSearch
   wrapper, which calls get_index / create_index on construction and so needs
   `Search Service Contributor` (index-definition rights). The ingest
   identity has that. The READ path (as_retriever) uses a raw SearchClient
   that only QUERIES documents — it never reads or mutates the index
   definition — so the public query app needs only `Search Index Data
   Reader`. Using one wrapper for both (the obvious shortcut) would force the
   internet-facing app to hold index-management rights it must never have.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_community.vectorstores.azuresearch import AzureSearch
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.config import settings
from app.providers import get_embeddings
from app.providers.azure_credential import get_azure_credential
from app.rag.retrieval.retriever_protocol import AccessTiers, EmptyRetriever

# Tier names come from directory names under data/corpus/company/. They should be
# lowercase ASCII. Validating here is defence in depth — if `permissions`
# from Laravel ever feed directly into tier strings, unescaped single
# quotes in an OData filter become injection. No native parameterised
# filters in Azure AI Search, so input validation is the only line.
_TIER_PATTERN = re.compile(r"^[a-zA-Z0-9 _-]+$")

# Index field names — fixed by the schema in AzureSearchVectorStore._build_store.
_VECTOR_FIELD = "content_vector"
_CONTENT_FIELD = "content"
_METADATA_FIELD = "metadata"


class _AzureSearchClientRetriever(BaseRetriever):
    """Read path: vector search over a raw SearchClient — documents only.

    Deliberately NOT the langchain AzureSearch wrapper: that wrapper calls
    get_index / create_index on construction (needs Search Service
    Contributor). This retriever only queries documents, so the public query
    app needs just Search Index Data Reader and can never touch the index
    definition. See module docstring point 5.

    Mirrors the wrapper's output shape: page_content from the `content` field,
    metadata from the JSON-serialised `metadata` field — so the rest of the
    chain (source_normalizer, etc.) sees identical Documents to the Chroma
    backend.
    """

    search_client: Any
    embeddings: Any
    k: int
    odata_filter: str | None = None
    query_mode: str = "vector"

    model_config = {"arbitrary_types_allowed": True}

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        import json

        from azure.search.documents.models import VectorizedQuery

        vector_query = VectorizedQuery(
            vector=self.embeddings.embed_query(query),
            k_nearest_neighbors=self.k,
            fields=_VECTOR_FIELD,
        )
        search_kwargs: dict[str, Any] = {
            "vector_queries": [vector_query],
            "filter": self.odata_filter,
            "top": self.k,
            "select": [_CONTENT_FIELD, _METADATA_FIELD],
        }
        if self.query_mode in ("hybrid", "hybrid_semantic"):
            search_kwargs["search_text"] = query
        else:
            search_kwargs["search_text"] = None
        if self.query_mode == "hybrid_semantic":
            search_kwargs["query_type"] = "semantic"
            search_kwargs["semantic_configuration_name"] = "default"
        results = self.search_client.search(**search_kwargs)
        docs: list[Document] = []
        for result in results:
            raw_meta = result.get(_METADATA_FIELD)
            metadata = json.loads(raw_meta) if raw_meta else {}
            docs.append(
                Document(
                    page_content=result.get(_CONTENT_FIELD) or "",
                    metadata=metadata,
                )
            )
        return docs


class AzureSearchVectorStore:
    """Satisfies both the Retriever and Indexer Protocols."""

    def __init__(
        self,
        index_name: str | None = None,
        *,
        request_timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        if not settings.azure_search_endpoint:
            raise RuntimeError(
                "retriever_kind='azure_search' but azure_search_endpoint is not set. "
                "Add it to .env."
            )
        self._index_name = index_name or settings.azure_search_index
        self._request_timeout_s = request_timeout_s
        self._max_retries = max_retries
        # The langchain wrapper is the WRITE path only (Indexer Protocol) and
        # is built lazily — its construction calls get_index/create_index,
        # which needs index-management rights the read-only query app lacks.
        # The read path (as_retriever) never touches it.
        self._store: AzureSearch | None = None

    def _get_write_store(self) -> AzureSearch:
        """Lazily build the langchain wrapper for the write path (ingest)."""
        if self._store is None:
            self._store = self._build_store()
        return self._store

    def _build_store(self) -> AzureSearch:
        """Build a fresh AzureSearch wrapper, auto-creating the index from
        the explicit schema on first call. Extracted so `clear()` can
        rebuild after dropping — otherwise `self._store` holds stale state
        pointing at a deleted index."""
        # Lazy import of Azure SDK schema types so this module can be
        # imported even when azure-search-documents isn't installed.
        # Anyone setting retriever_kind=azure_search has already installed
        # the optional [azure] extra.
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            SearchableField,
            SearchField,
            SearchFieldDataType,
            SemanticConfiguration,
            SemanticField,
            SemanticPrioritizedFields,
            SemanticSearch,
            SimpleField,
            VectorSearch,
            VectorSearchProfile,
        )

        embeddings = get_embeddings()

        # Explicit schema. access_tier MUST be filterable — this is the
        # security boundary (Layer 1 of the two-layer access control,
        # CLAUDE.md). Drop filterable=True here and tier filtering
        # silently no-ops.
        fields = [
            SimpleField(
                name="id",
                type=SearchFieldDataType.String,
                key=True,
                filterable=True,
            ),
            SearchableField(
                name="content",
                type=SearchFieldDataType.String,
            ),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=1536,  # text-embedding-3-small
                vector_search_profile_name="default",
            ),
            SimpleField(
                name="metadata",
                type=SearchFieldDataType.String,
            ),
            # Hoisted from Document.metadata["access_tier"] by the wrapper
            # so it's queryable at the top level (filterable column, not
            # opaque JSON blob).
            SimpleField(
                name="access_tier",
                type=SearchFieldDataType.String,
                filterable=True,
            ),
            SimpleField(
                name="source",
                type=SearchFieldDataType.String,
                filterable=True,
            ),
            # Hoisted provenance fields (doc_id, block_kind, ingested_at) for
            # OData filtering — e.g. reindex gate queries by ingest run.
            SimpleField(
                name="doc_id",
                type=SearchFieldDataType.String,
                filterable=True,
            ),
            SimpleField(
                name="block_kind",
                type=SearchFieldDataType.String,
                filterable=True,
            ),
            SimpleField(
                name="ingested_at",
                type=SearchFieldDataType.String,
                filterable=True,
            ),
        ]

        # Vector search algorithm + profile. The `content_vector` field
        # above references profile name "default"; this config defines it.
        # Without it, langchain-community's AzureSearch wrapper fails with
        # UnknownVectorAlgorithmConfiguration on index creation. (This is
        # the Gap-1 wrapper-version mismatch flagged in the progress doc.)
        vector_search = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-default")],
            profiles=[
                VectorSearchProfile(
                    name="default",
                    algorithm_configuration_name="hnsw-default",
                )
            ],
        )
        semantic_search = SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name="default",
                    prioritized_fields=SemanticPrioritizedFields(
                        content_fields=[SemanticField(field_name="content")]
                    ),
                )
            ]
        )

        return AzureSearch(
            azure_search_endpoint=settings.azure_search_endpoint,
            # Managed identity (Entra ID), not a key. key=None makes the wrapper
            # use the credential we pass; the shared DefaultAzureCredential is
            # reused across all Azure clients. See app/providers/azure_credential.
            azure_search_key=None,
            azure_credential=get_azure_credential(),
            index_name=self._index_name,
            # Pass the bound method, not the Embeddings instance — older
            # langchain-community versions require a plain callable.
            embedding_function=embeddings.embed_query,
            fields=fields,
            vector_search=vector_search,
            semantic_search=semantic_search,
        )

    # ---- Retriever Protocol -------------------------------------------------

    def as_retriever(self, k: int, access_tiers: AccessTiers) -> BaseRetriever:
        """Build a read-only retriever (raw SearchClient, documents only).

        See `Retriever` Protocol for the three-state access_tiers contract:
        None -> no filter (admin); [] -> EmptyRetriever (zero perms);
        [...] -> OData tier filter. The query never reads the index
        definition, so Search Index Data Reader is sufficient (point 5).
        """
        if access_tiers is None:
            odata_filter: str | None = None
        elif not access_tiers:
            # Caller has zero permissions. Short-circuit — don't build an
            # empty OData filter and hope Azure interprets it as "match
            # nothing". Symmetric with the Chroma backend.
            return EmptyRetriever()
        else:
            odata_filter = self._build_filter(access_tiers)

        return _AzureSearchClientRetriever(
            search_client=self._build_search_client(),
            embeddings=get_embeddings(
                request_timeout_s=self._request_timeout_s,
                max_retries=self._max_retries,
            ),
            k=k,
            odata_filter=odata_filter,
            query_mode=settings.azure_search_query_mode,
        )

    def _build_search_client(self):
        """Raw SearchClient for the read path — document queries only, so it
        needs just Search Index Data Reader. Never calls get_index /
        create_index, unlike the langchain AzureSearch wrapper."""
        from azure.search.documents import SearchClient

        kwargs: dict[str, Any] = {}
        if self._request_timeout_s is not None or self._max_retries is not None:
            from azure.core.pipeline.policies import RetryPolicy

            timeout_s = self._request_timeout_s if self._request_timeout_s is not None else 2.0
            retries = self._max_retries if self._max_retries is not None else 0
            kwargs["connection_timeout"] = timeout_s
            kwargs["read_timeout"] = timeout_s
            kwargs["retry_policy"] = RetryPolicy(
                retry_total=retries,
                retry_connect=retries,
                retry_read=retries,
                retry_status=retries,
                timeout=timeout_s,
            )
        return SearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=self._index_name,
            credential=get_azure_credential(),
            **kwargs,
        )

    # ---- Indexer Protocol ---------------------------------------------------

    def add_documents(self, documents: list[Document]) -> None:
        """Embed and store document chunks in batches (write path)."""
        from app.rag.retrieval.retriever_protocol import batched_add

        batched_add(self._get_write_store(), documents)

    def clear(self) -> None:
        """
        Drop the index entirely and rebuild a fresh AzureSearch wrapper.

        Why drop + recreate rather than delete-by-query? Azure delete-by-id
        is eventually consistent and slow at our scale. Recreating is the
        only way to guarantee a clean slate — Chroma's reset_collection()
        does the same shape thing.

        The wrapper rebuild is essential: `self._store` was instantiated
        against the old (now-deleted) index. Without re-running _build_store(),
        the next `add_documents()` call hits ResourceNotFoundError because
        the wrapper still thinks the index exists. _build_store() also
        triggers index re-creation from the schema on first add.
        """
        from azure.core.exceptions import ResourceNotFoundError
        from azure.search.documents.indexes import SearchIndexClient

        # Same shared managed-identity credential as the data-plane store.
        # NB: delete/create index needs the `Search Service Contributor` role
        # (index lifecycle), which is separate from `Search Index Data
        # Contributor` (document read/write). The ingest principal needs both.
        client = SearchIndexClient(
            endpoint=settings.azure_search_endpoint,
            credential=get_azure_credential(),
        )
        try:
            client.delete_index(self._index_name)
        except ResourceNotFoundError:
            # First-time ingest — nothing to clear.
            pass

        # Rebuild the wrapper so the next add_documents() recreates the index.
        self._store = self._build_store()

    # ---- Internal -----------------------------------------------------------

    @staticmethod
    def _build_filter(access_tiers: list[str]) -> str:
        """
        Translate a list of tier names into an OData filter expression.

        Chroma equivalent: {"access_tier": {"$in": ["sales", "finance"]}}
        Azure OData:       "search.in(access_tier, 'sales,finance', ',')"

        Why search.in() instead of "access_tier eq 'sales' or access_tier
        eq 'finance'"? search.in() is the documented Azure idiom for $in
        semantics — server-side parsed, no length limit issues, and the
        delimiter is explicit (we pass ',').
        """
        for tier in access_tiers:
            if not _TIER_PATTERN.fullmatch(tier):
                raise ValueError(
                    f"Refusing to build OData filter from suspicious tier name: {tier!r}"
                )
        joined = ",".join(access_tiers)
        return f"search.in(access_tier, '{joined}', ',')"
