"""Chunk-level provenance stamps (ADR 0017 §4): every chunk answers
'which document version, which run, which index produced you'."""

import hashlib
from datetime import UTC, datetime

from langchain_core.documents import Document


def stamp_chunks(chunks: list[Document], *, run_id: str, index_name: str) -> None:
    ingested_at = datetime.now(UTC).isoformat(timespec="seconds")
    counters: dict[str, int] = {}
    for chunk in chunks:
        source = chunk.metadata.get("source", "")
        i = counters.get(source, 0)
        counters[source] = i + 1
        chunk.metadata["chunk_index"] = i
        doc_id = chunk.metadata.get("doc_id", source)
        digest = hashlib.sha256(f"{doc_id}|{i}|{chunk.page_content}".encode()).hexdigest()
        chunk.metadata["chunk_id"] = digest[:32]
        chunk.metadata["ingested_at"] = ingested_at
        chunk.metadata["ingest_run_id"] = run_id
        chunk.metadata["index_name"] = index_name
