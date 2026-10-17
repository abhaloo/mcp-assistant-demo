"""Owns one attempt directory: exclusive writes, never overwrites, hashes at the end.

Moved verbatim from rag_experiment.py:
  - `_json_ready`            :266-276
  - `_write_json_exclusive`  :279-282
  - `_write_jsonl_exclusive` :285-290
  - attempt-dir creation     :483-498
  - artifact-hash sweep      :814-824

S1 (harness-core) slice of docs/superpowers/plans/2026-08-01-harness-unify-implementation-plan.md.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.experiments.core.answer_persistence import sanitize_answer_rows_for_persistence
from app.experiments.core.atomic_io import atomic_write_text_exclusive
from app.experiments.core.errors import RagExperimentError
from app.experiments.core.hashing import sha256_file


class AttemptExistsError(RagExperimentError):
    """Raised when an attempt id is already present on disk.

    Subclasses RagExperimentError (not a fresh exception hierarchy) so every
    existing `except RagExperimentError` clause -- including the
    tests/experiments/test_rag_experiment.py assertions -- keeps catching it
    unchanged.
    """


def _json_ready(value: Any) -> Any:
    """Convert non-finite provider values to JSON null without changing evidence meaning."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    return value


def _write_json_exclusive(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as file_handle:
        json.dump(_json_ready(value), file_handle, ensure_ascii=False, indent=2, sort_keys=True)
        file_handle.write("\n")


def _write_jsonl_exclusive(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("x", encoding="utf-8") as file_handle:
        for row in rows:
            file_handle.write(
                json.dumps(_json_ready(row), ensure_ascii=False, sort_keys=True) + "\n"
            )


class AttemptStore:
    """Owns one attempt directory: exclusive writes, never overwrites, hashes at the end."""

    def __init__(self, attempt_root: Path, attempt_id: str) -> None:
        self.dir = attempt_root / attempt_id
        try:
            self.dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise AttemptExistsError(f"attempt already exists: {self.dir}") from exc

    @classmethod
    def open_existing(cls, attempt_dir: Path) -> AttemptStore:
        """Bind to an attempt directory that already exists, creating nothing."""
        store = cls.__new__(cls)
        store.dir = attempt_dir
        return store

    def write_json(self, name: str, value: Any) -> None:
        _write_json_exclusive(self.dir / name, value)

    def write_jsonl(self, name: str, rows: list[dict[str, Any]]) -> None:
        _write_jsonl_exclusive(self.dir / name, rows)

    def write_sealed_text(self, name: str, text: str) -> None:
        """Write a sealed artifact once via atomic temp+replace; never overwrite."""
        atomic_write_text_exclusive(self.dir / name, text)

    def exists(self, name: str) -> bool:
        return (self.dir / name).exists()

    def hash_artifacts(self) -> dict[str, str]:
        """sha256 every file except artifact_hashes.json itself, then write that."""
        artifact_hashes: dict[str, str] = {}
        for path in sorted(self.dir.iterdir(), key=lambda item: item.name):
            if path.name == "artifact_hashes.json" or not path.is_file():
                continue
            artifact_hashes[path.name] = sha256_file(path)
        self.write_json("artifact_hashes.json", artifact_hashes)
        return artifact_hashes

    def seal_verdict(
        self,
        *,
        scored_rows: list[dict[str, Any]],
        oracle_validation: object,
        verdict: object,
        oracle_validation_filename: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write one attempt's scored rows, oracle validation and verdict."""
        self.write_jsonl(
            "scores.jsonl",
            sanitize_answer_rows_for_persistence(scored_rows),
        )
        if hasattr(oracle_validation, "to_dict"):
            oracle_dict = oracle_validation.to_dict()
        elif hasattr(oracle_validation, "__dataclass_fields__"):
            oracle_dict = asdict(oracle_validation)
        elif isinstance(oracle_validation, dict):
            oracle_dict = dict(oracle_validation)
        else:
            oracle_dict = oracle_validation
        self.write_json(oracle_validation_filename, oracle_dict)

        if extra:
            for filename, content in extra.items():
                self.write_json(filename, content)

        if hasattr(verdict, "to_dict"):
            verdict_dict = verdict.to_dict()
        elif hasattr(verdict, "__dataclass_fields__"):
            verdict_dict = asdict(verdict)
        elif isinstance(verdict, dict):
            verdict_dict = dict(verdict)
        else:
            verdict_dict = {"status": str(verdict)}
        self.write_json("verdict.json", verdict_dict)


def hash_tree_artifacts(
    root: Path,
    *,
    exclude_names: frozenset[str] = frozenset({"acceptance-ledger.json", "heartbeat.json"}),
) -> dict[str, str]:
    """sha256 every file under root (relative paths), excluding named leaf files."""
    return {
        str(path.relative_to(root)).replace("\\", "/"): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in exclude_names
    }
