"""
Offline deterministic embedding provider for cloud E2E and hermetic ingest tests.

Produces stable, network-free vectors from text content so document-RAG plumbing
can be validated without OpenAI/Azure credentials or spend.
"""

from __future__ import annotations

import hashlib
import struct

from langchain_core.embeddings import Embeddings

_DEFAULT_DIMENSIONS = 384


def _digest_to_vector(text: str, dimensions: int) -> list[float]:
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    counter = 0
    while len(values) < dimensions:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
        for offset in range(0, len(block), 4):
            if len(values) >= dimensions:
                break
            (raw,) = struct.unpack(">I", block[offset : offset + 4])
            values.append((raw / 0xFFFFFFFF) * 2.0 - 1.0)
    return values


class OfflineEmbeddings(Embeddings):
    """SHA256-expanded deterministic vectors — same input always same embedding."""

    def __init__(self, dimensions: int = _DEFAULT_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_digest_to_vector(text, self.dimensions) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return _digest_to_vector(text, self.dimensions)


def get_embeddings() -> Embeddings:
    return OfflineEmbeddings()
