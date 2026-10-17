"""Ingest run manifest — the auditable record of what a build contains.

Answers 'which files, which versions, how many chunks, what settings produced
index X' — the record whose absence let two .xlsx files go missing for months.
"""

import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from langchain_core.documents import Document

from app.config import settings
from app.rag.ingestion.loader import LoadReport

DEFAULT_OUT_DIR = Path("data/ingest-runs")


def build_manifest(
    *,
    run_id: str,
    index_name: str,
    report: LoadReport,
    chunks: list[Document],
    parser_kind: str,
) -> dict:
    per_source = Counter(c.metadata.get("source", "") for c in chunks)
    return {
        "run_id": run_id,
        "index_name": index_name,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "parser_kind": parser_kind,
        "embedding_model": settings.embedding_model,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "totals": {
            "files_loaded": len(report.loaded),
            "files_failed": len(report.failed),
            "files_unsupported": len(report.unsupported),
            "chunks": len(chunks),
        },
        "files": [asdict(f) | {"chunks": per_source.get(f.path, 0)} for f in report.files],
    }


def write_manifest(manifest: dict, out_dir: Path | None = None) -> Path:
    out = out_dir or DEFAULT_OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{manifest['run_id']}.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
