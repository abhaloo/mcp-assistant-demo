"""
Document ingestion script.

Run this to load documents from data/corpus/company/ into ChromaDB.
You run this ONCE (or whenever documents change), not on every API startup.

Usage:
    python scripts/deploy/ingest.py
    python scripts/deploy/ingest.py --docs-dir /path/to/your/docs
"""

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.rag.ingestion.loader import load_documents, split_documents
from app.rag.ingestion.manifest import build_manifest, write_manifest
from app.rag.ingestion.provenance import stamp_chunks
from app.rag.retrieval.retriever_factory import get_indexer


def _corpus_container_client():
    from azure.storage.blob import ContainerClient

    from app.providers.azure_credential import get_azure_credential

    return ContainerClient(
        account_url=settings.corpus_blob_account_url,
        container_name=settings.corpus_blob_container,
        credential=get_azure_credential(),
    )


def download_corpus_to_temp() -> Path:
    """Pull the blob corpus into a temp dir preserving tier prefixes (= dirs)."""
    import tempfile

    client = _corpus_container_client()
    root = Path(tempfile.mkdtemp(prefix="corpus-"))
    count = 0
    for blob in client.list_blobs():
        dest = root / blob.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(client.download_blob(blob.name).readall())
        count += 1
    if count == 0:
        raise SystemExit("Corpus container is empty — refusing to build an empty index.")
    print(f"  downloaded {count} blobs to {root}")
    return root


def run_ingest(
    docs_dir: str | None,
    parser_kind: str | None,
    collection: str,
    allow_partial: bool = False,
) -> dict:
    start = time.time()

    print("=== Document Ingestion Pipeline ===\n")
    if docs_dir is None and settings.corpus_source == "azure_blob":
        docs_dir = str(download_corpus_to_temp())
    print(f"Source directory: {docs_dir or settings.corpus_dir}")
    print(f"Parser: {parser_kind or settings.parser_kind}")

    target = collection
    indexer = get_indexer(collection_name=collection)
    indexer.clear()

    print("\n[1/3] Loading documents...")
    documents, report = load_documents(docs_dir, parser_kind=parser_kind)
    for line in report.summary_lines():
        print(f"  {line}")
    if report.failed and not allow_partial:
        raise SystemExit(
            f"{len(report.failed)} file(s) failed to load — aborting so the index can't silently "
            "miss documents. Fix them or rerun with --allow-partial."
        )
    if not documents:
        print("No documents found. Exiting.")
        print(f"Add .md, .pdf, or .txt files to {docs_dir or settings.corpus_dir}/")
        return {}

    print(f"  -> {len(documents)} documents loaded")

    print("\n[2/3] Splitting into chunks...")
    chunks = split_documents(documents)
    run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}"
    stamp_chunks(chunks, run_id=run_id, index_name=target)
    provenance = {
        "parser_kind": parser_kind or settings.parser_kind,
        "embedding_model": settings.embedding_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }
    for chunk in chunks:
        chunk.metadata.update(provenance)
    print(f"  -> {len(chunks)} chunks created")

    print("\n[3/3] Embedding and storing...")
    indexer.add_documents(chunks)

    manifest = build_manifest(
        run_id=run_id,
        index_name=target,
        report=report,
        chunks=chunks,
        parser_kind=parser_kind or settings.parser_kind,
    )
    print(f"\nManifest: {write_manifest(manifest)}")

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed:.1f}s ===")
    print(f"Collection/index: {target}")
    print("You can now run: uvicorn app.main:app --reload")
    print("Then POST to /api/ask with a question.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Ingest documents into vector store")
    parser.add_argument(
        "--docs-dir",
        default=None,
        help="Directory containing documents to ingest",
    )
    parser.add_argument(
        "--parser",
        default=None,
        help="parser backend: pypdf|unstructured (default: settings.parser_kind)",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="target collection/index (default: settings.collection_name)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="override prod wipe guard for Azure in-place clear",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="proceed even if some files failed to load/parse (default: abort)",
    )
    args = parser.parse_args()

    target = args.collection or settings.collection_name
    if settings.retriever_kind == "azure_search" and args.collection is None and not args.force:
        raise SystemExit(
            "Refusing to clear the live Azure index in place (ADR 0017: blue-green only).\n"
            "Ingest to a versioned index with --collection <name>, validate, then repoint\n"
            "the alias/config. Pass --force to override deliberately."
        )

    run_ingest(
        args.docs_dir,
        args.parser,
        collection=target,
        allow_partial=args.allow_partial,
    )


if __name__ == "__main__":
    main()
