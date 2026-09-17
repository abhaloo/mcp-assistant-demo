"""Document loading, parsing, and chunking pipeline."""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from langchain_community.document_loaders import TextLoader, UnstructuredMarkdownLoader
from langchain_core.documents import Document

from app.config import settings
from app.rag.ingestion.parser_factory import ParsedBlock, get_parser
from app.rag.ingestion.splitter_factory import get_splitter

# Directory names on disk may differ from access-tier labels (e.g. graphic-design → graphic design).
TIER_DIR_ALIASES: dict[str, str] = {
    "graphic-design": "graphic design",
}

SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf", ".xlsx"}


@dataclass
class FileRecord:
    path: str
    tier: str
    status: Literal["loaded", "failed", "unsupported"]
    content_hash: str = ""
    error: str = ""


@dataclass
class LoadReport:
    files: list[FileRecord] = field(default_factory=list)

    @property
    def loaded(self) -> list[FileRecord]:
        return [f for f in self.files if f.status == "loaded"]

    @property
    def failed(self) -> list[FileRecord]:
        return [f for f in self.files if f.status == "failed"]

    @property
    def unsupported(self) -> list[FileRecord]:
        return [f for f in self.files if f.status == "unsupported"]

    def summary_lines(self) -> list[str]:
        lines = [
            f"{len(self.loaded)} loaded, {len(self.failed)} failed, "
            f"{len(self.unsupported)} unsupported"
        ]
        lines += [f"FAILED      {f.path}: {f.error}" for f in self.failed]
        lines += [
            f"UNSUPPORTED {f.path}: {f.error}"
            if f.error
            else f"UNSUPPORTED {f.path} (no loader for this type)"
            for f in self.unsupported
        ]
        return lines


# ---------------------------------------------------------------------
# Loader: reads raw files into LangChain Document objects
# ---------------------------------------------------------------------
def load_documents(
    docs_dir: str | None = None, parser_kind: str | None = None
) -> tuple[list[Document], LoadReport]:
    docs_path = Path(docs_dir or settings.corpus_dir)
    kind = parser_kind or settings.parser_kind
    all_docs: list[Document] = []
    report = LoadReport()

    for tier_dir in sorted(p for p in docs_path.iterdir() if p.is_dir()):
        tier = TIER_DIR_ALIASES.get(tier_dir.name, tier_dir.name)
        for path in sorted(p for p in tier_dir.rglob("*") if p.is_file()):
            suffix = path.suffix.lower()
            try:
                content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except Exception as e:
                report.files.append(
                    FileRecord(str(path), tier, "failed", "", f"{type(e).__name__}: {e}")
                )
                continue
            if suffix not in SUPPORTED_SUFFIXES:
                report.files.append(FileRecord(str(path), tier, "unsupported", content_hash))
                continue
            try:
                docs = _load_one(path, suffix, tier, kind)
                for doc in docs:
                    doc.metadata["access_tier"] = tier
                    doc.metadata["doc_id"] = path.relative_to(docs_path).as_posix()
                    doc.metadata["content_hash"] = content_hash
                all_docs.extend(docs)
                report.files.append(FileRecord(str(path), tier, "loaded", content_hash))
            except Exception as e:  # per-file: one bad file never hides the rest
                report.files.append(
                    FileRecord(str(path), tier, "failed", content_hash, f"{type(e).__name__}: {e}")
                )

    for path in sorted(p for p in docs_path.iterdir() if p.is_file()):
        # Root-level files have no tier and are never ingested — but the coverage
        # guarantee says nothing goes missing SILENTLY, so report them.
        try:
            content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as e:
            report.files.append(FileRecord(str(path), "", "failed", "", f"{type(e).__name__}: {e}"))
            continue
        report.files.append(
            FileRecord(
                str(path),
                "",
                "unsupported",
                content_hash,
                "file at corpus root — place it inside a tier folder",
            )
        )
    return all_docs, report


def _load_one(path: Path, suffix: str, tier: str, parser_kind: str) -> list[Document]:
    if suffix == ".md":
        if settings.md_header_chunking:
            return TextLoader(str(path), encoding="utf-8").load()
        return UnstructuredMarkdownLoader(str(path)).load()
    if suffix == ".txt":
        return TextLoader(str(path)).load()
    if suffix == ".xlsx":
        blocks = get_parser("excel", {}).parse(path)
        return blocks_to_documents(blocks, tier=tier, source=str(path))
    # .pdf → pluggable parser backend (unchanged routing)
    blocks = get_parser(parser_kind, {}).parse(path)
    return blocks_to_documents(blocks, tier=tier, source=str(path))


def blocks_to_documents(blocks: list[ParsedBlock], *, tier: str, source: str) -> list[Document]:
    """Convert parser output into LangChain Documents, tagging the access tier
    and preserving block kind + raw table HTML in metadata."""
    docs: list[Document] = []
    for block in blocks:
        metadata = {
            **block.metadata,
            "access_tier": tier,
            "source": source,
            "block_kind": block.kind,
        }
        if block.kind == "table" and block.html:
            metadata["table_html"] = block.html
        # Chroma rejects None metadata values — strip missing optional fields
        # (e.g. Unstructured elements with no page_number).
        metadata = {k: v for k, v in metadata.items() if v is not None}
        docs.append(Document(page_content=block.content, metadata=metadata))
    return docs


# ---------------------------------------------------------------------
# Splitter: breaks documents into retrieval-sized chunks
# ---------------------------------------------------------------------
def _join_section(metadata: dict) -> str | None:
    parts = [metadata[k] for k in ("h1", "h2", "h3") if k in metadata and metadata[k]]
    if not parts:
        return None
    section = " > ".join(parts)
    for key in ("h1", "h2", "h3"):
        metadata.pop(key, None)
    return section


def split_documents(documents: list[Document]) -> list[Document]:
    """Split text documents into chunks; pass table blocks through untouched."""
    kind = "markdown_header_recursive" if settings.md_header_chunking else "recursive"
    splitter = get_splitter(
        kind,
        {"chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap},
    )

    tables = [d for d in documents if d.metadata.get("block_kind") == "table"]
    splittable = [d for d in documents if d.metadata.get("block_kind") != "table"]

    chunks = splitter.split_documents(splittable)
    chunks.extend(tables)

    if settings.md_header_chunking:
        for doc in chunks:
            section = _join_section(doc.metadata)
            if section:
                doc.metadata["section"] = section

    return chunks


# ---------------------------------------------------------------------
# CLI entry point: python -m app.rag.ingestion.loader
# ---------------------------------------------------------------------
if __name__ == "__main__":
    print("Loading documents...")
    docs, report = load_documents()
    for line in report.summary_lines():
        print(f"  {line}")
    print(f"\nLoaded {len(docs)} documents total")

    if docs:
        print("\nSplitting into chunks...")
        chunks = split_documents(docs)
        print(f"Created {len(chunks)} chunks")

        # Show a sample chunk so you can see what you're working with
        print("\n--- Sample chunk (index 0) ---")
        print(f"Content ({len(chunks[0].page_content)} chars):")
        print(chunks[0].page_content[:500])
        print(f"\nMetadata: {chunks[0].metadata}")
    else:
        print(f"\nNo documents found. Add files to {settings.corpus_dir}/ first.")
        print("Supported formats: .md, .pdf, .txt, .xlsx")
