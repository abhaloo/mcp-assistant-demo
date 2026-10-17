"""Durable campaign artifact export and supervisor termination records."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.experiments.core.atomic_io import atomic_write_text_exclusive
from app.experiments.core.hashing import sha256_file, sha256_json

__all__ = [
    "ArtifactExportReceipt",
    "TerminationRecord",
    "export_campaign_artifacts",
    "write_termination_record",
]

_EXCLUDED_EXPORT_SUFFIXES = {".sql"}
_EXCLUDED_EXPORT_NAMES = {"mcp_local.sql"}


@dataclass(frozen=True)
class TerminationRecord:
    exit_code: int
    signal: str | None
    pid: int
    run_id: str
    last_sealed_stage: str | None
    partial_artifact_hashes: dict[str, str]
    last_receipt_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactExportReceipt:
    source_root: str
    destination_root: str
    source_hashes: dict[str, str]
    destination_hashes: dict[str, str]
    complete: bool
    receipt_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _collect_export_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.name in _EXCLUDED_EXPORT_NAMES or path.suffix.lower() in _EXCLUDED_EXPORT_SUFFIXES:
            continue
        files.append(path)
    return files


def export_campaign_artifacts(source: Path, destination: Path) -> ArtifactExportReceipt:
    """Copy sealed campaign artifacts and verify destination hashes."""
    if not source.is_dir():
        raise FileNotFoundError(f"source run directory not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    source_hashes: dict[str, str] = {}
    destination_hashes: dict[str, str] = {}
    for path in _collect_export_files(source):
        rel = path.relative_to(source).as_posix()
        source_hashes[rel] = sha256_file(path)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        destination_hashes[rel] = sha256_file(target)
    complete = source_hashes == destination_hashes and bool(source_hashes)
    body = {
        "source_root": str(source),
        "destination_root": str(destination),
        "source_hashes": source_hashes,
        "destination_hashes": destination_hashes,
        "complete": complete,
    }
    receipt_hash = sha256_json(body)
    return ArtifactExportReceipt(receipt_hash=receipt_hash, **body)


def write_termination_record(
    attempt_dir: Path,
    *,
    exit_code: int,
    signal: str | None = None,
    pid: int,
    run_id: str,
    last_sealed_stage: str | None,
    partial_artifact_hashes: dict[str, str] | None = None,
    last_receipt_hash: str | None = None,
) -> TerminationRecord:
    record = TerminationRecord(
        exit_code=exit_code,
        signal=signal,
        pid=pid,
        run_id=run_id,
        last_sealed_stage=last_sealed_stage,
        partial_artifact_hashes=partial_artifact_hashes or {},
        last_receipt_hash=last_receipt_hash,
    )
    payload = {
        **record.to_dict(),
        "written_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text_exclusive(
        attempt_dir / "termination.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return record
