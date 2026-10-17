"""
LanceDB hybrid (FTS + dense vector) implementation of Retriever + Indexer Protocols.

Hybrid read path:
    table.search(query_type="hybrid").vector(query_vec).text(query)
        .where(sql, prefilter=True).limit(k)
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import uuid
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.config import settings
from app.providers import get_embeddings
from app.rag.retrieval.retriever_protocol import AccessTiers, EmptyRetriever

logger = logging.getLogger(__name__)

_TIER_PATTERN = re.compile(r"^[a-zA-Z0-9 _-]+$")
_BATCH_SIZE = 50
_PROVENANCE_KEYS = frozenset({"source", "chunk_index", "title", "access_tier", "chunk_id"})


class _LanceHybridRetriever(BaseRetriever):
    """Hybrid BM25 + vector search with optional tier prefilter."""

    table: Any
    embeddings: Any
    k: int
    where_sql: str | None = None
    vector_dim: int | None = None

    model_config = {"arbitrary_types_allowed": True}

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        query_vec = self.embeddings.embed_query(query)
        if self.vector_dim is not None and len(query_vec) != self.vector_dim:
            raise ValueError(
                f"Query vector dimension {len(query_vec)} != store dimension {self.vector_dim}"
            )
        _require_fts(self.table)

        builder = self.table.search(query_type="hybrid").vector(query_vec).text(query)
        if self.where_sql is not None:
            builder = builder.where(self.where_sql, prefilter=True)
        rows = builder.limit(self.k).to_list()
        return [_row_to_document(row) for row in rows]


class LanceDBVectorStore:
    """Satisfies both Retriever and Indexer Protocols via LanceDB hybrid search."""

    def __init__(
        self,
        collection_name: str | None = None,
        *,
        request_timeout_s: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        self._collection_name = collection_name or settings.collection_name
        self._request_timeout_s = request_timeout_s
        self._max_retries = max_retries
        self._db: Any | None = None
        self._table: Any | None = None
        self._embeddings: Any | None = None
        self._vector_dim: int | None = None

    def _connect(self) -> Any:
        if self._db is None:
            import lancedb

            self._db = lancedb.connect(settings.lancedb_persist_dir)
        return self._db

    def _embeddings_fn(self) -> Any:
        if self._embeddings is None:
            self._embeddings = get_embeddings(
                request_timeout_s=self._request_timeout_s,
                max_retries=self._max_retries,
            )
        return self._embeddings

    def _open_table(self) -> Any:
        db = self._connect()
        if self._table is not None:
            return self._table
        if self._collection_name not in _table_names(db):
            raise RuntimeError(f"Lance table {self._collection_name!r} does not exist")
        self._table = db.open_table(self._collection_name)
        self._vector_dim = _infer_vector_dim(self._table)
        return self._table

    def as_retriever(self, k: int, access_tiers: AccessTiers) -> BaseRetriever:
        if access_tiers is None:
            where_sql = None
        elif not access_tiers:
            return EmptyRetriever()
        else:
            where_sql = _build_where(access_tiers)

        table = self._open_table()
        return _LanceHybridRetriever(
            table=table,
            embeddings=self._embeddings_fn(),
            k=k,
            where_sql=where_sql,
            vector_dim=self._vector_dim,
        )

    def add_documents(self, documents: list[Document]) -> None:
        if not documents:
            return

        embeddings = self._embeddings_fn()
        texts = [doc.page_content for doc in documents]
        vectors = embeddings.embed_documents(texts)
        if vectors:
            self._vector_dim = len(vectors[0])

        rows = [
            _document_to_row(doc, vector) for doc, vector in zip(documents, vectors, strict=True)
        ]
        db = self._connect()
        table = self._table

        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            if table is None:
                if self._collection_name in _table_names(db):
                    table = db.open_table(self._collection_name)
                    table.add(batch)
                else:
                    table = db.create_table(self._collection_name, data=batch)
                self._table = table
            else:
                table.add(batch)
            logger.info("Ingested Lance batch %d (%d chunks)", i // _BATCH_SIZE + 1, len(batch))

        table.create_fts_index("text", replace=True)

    def clear(self) -> None:
        db = self._connect()
        if self._collection_name in _table_names(db):
            db.drop_table(self._collection_name)
        self._table = None
        self._vector_dim = None

    def ping(self) -> None:
        db = self._connect()
        if self._collection_name not in _table_names(db):
            raise RuntimeError(f"Lance table {self._collection_name!r} is missing")
        table = db.open_table(self._collection_name)
        if table.count_rows() == 0:
            raise RuntimeError(f"Lance table {self._collection_name!r} is empty")


def _build_where(access_tiers: list[str]) -> str:
    for tier in access_tiers:
        if not _TIER_PATTERN.fullmatch(tier):
            raise ValueError(f"Refusing to build filter from suspicious tier name: {tier!r}")
    joined = ", ".join(f"'{tier}'" for tier in access_tiers)
    return f"access_tier IN ({joined})"


def _document_to_row(doc: Document, vector: list[float]) -> dict[str, Any]:
    meta = dict(doc.metadata)
    chunk_id = meta.pop("chunk_id", None) or str(uuid.uuid4())
    access_tier = meta.pop("access_tier", "unknown")
    source = meta.pop("source", "unknown")
    chunk_index = meta.pop("chunk_index", None)
    title = meta.pop("title", None)
    extra = {k: v for k, v in meta.items() if k not in _PROVENANCE_KEYS}
    row: dict[str, Any] = {
        "id": chunk_id,
        "text": doc.page_content,
        "vector": vector,
        "access_tier": access_tier,
        "source": source,
    }
    if chunk_index is not None:
        row["chunk_index"] = chunk_index
    if title is not None:
        row["title"] = title
    if extra:
        row["metadata"] = json.dumps(extra)
    return row


def _row_to_document(row: dict[str, Any]) -> Document:
    metadata: dict[str, Any] = {
        "source": row.get("source", "unknown"),
        "access_tier": row.get("access_tier", "unknown"),
    }
    if row.get("chunk_index") is not None:
        metadata["chunk_index"] = row["chunk_index"]
    if row.get("title") is not None:
        metadata["title"] = row["title"]
    if row.get("metadata"):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            metadata.update(json.loads(row["metadata"]))
    return Document(page_content=row.get("text", ""), metadata=metadata)


def _table_names(db: Any) -> list[str]:
    names = db.table_names()
    return [name if isinstance(name, str) else getattr(name, "name", str(name)) for name in names]


def _require_fts(table: Any) -> None:
    lister = getattr(table, "list_indices", None)
    if lister is None:
        return
    indices = list(lister())
    for idx in indices:
        blob = " ".join(
            str(part)
            for part in (
                getattr(idx, "index_type", None),
                getattr(idx, "name", None),
                getattr(idx, "columns", None),
                idx,
            )
            if part is not None
        ).lower()
        if "fts" in blob or "inverted" in blob or "full text" in blob or "full_text" in blob:
            return
    raise RuntimeError("Lance FTS index is missing")


def _infer_vector_dim(table: Any) -> int | None:
    sample = table.head(1).to_pylist()
    if not sample:
        return None
    vector = sample[0].get("vector")
    if vector is None:
        return None
    return len(vector)
