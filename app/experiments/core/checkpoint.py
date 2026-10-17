"""Judge checkpoint sealing and verification before verdict."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.experiments.core.attempt_store import AttemptStore
from app.experiments.core.errors import RagExperimentError
from app.experiments.core.hashing import sha256_bytes, sha256_json
from app.experiments.core.jsonl import read_jsonl
from app.experiments.core.protocol import OracleValidation
from app.experiments.core.stage_receipts import derive_terminal_state

__all__ = [
    "CheckpointError",
    "JudgeCheckpoint",
    "RecoverableCheckpointAbort",
    "VerifiedCheckpoint",
    "cell_set_hash",
    "maybe_raise_recoverable_checkpoint",
    "seal_judge_checkpoint",
    "verify_recoverable_checkpoint",
]


class CheckpointError(RagExperimentError):
    """Checkpoint evidence is incomplete, duplicated, or hash-mismatched."""


@dataclass(frozen=True)
class RecoverableCheckpointAbort(Exception):
    """Raised when a sealed judge checkpoint can be finalized without replay."""

    attempt_dir: Path
    required_action: str

    def __str__(self) -> str:
        return (
            f"recoverable checkpoint at {self.attempt_dir} (required_action={self.required_action})"
        )


def maybe_raise_recoverable_checkpoint(attempt_dir: Path) -> None:
    terminal = derive_terminal_state(attempt_dir)
    if terminal.name == "RECOVERABLE_CHECKPOINT":
        raise RecoverableCheckpointAbort(
            attempt_dir=attempt_dir,
            required_action=str(terminal.required_action or "finalize-from-checkpoint"),
        )


@dataclass(frozen=True)
class JudgeCheckpoint:
    input_hashes: dict[str, str]
    output_hashes: dict[str, str]
    spend_evidence: dict[str, Any]
    path: Path


@dataclass(frozen=True)
class VerifiedCheckpoint:
    recoverable: bool
    checkpoint_dir: Path


def cell_set_hash(cell_keys: list[tuple[str, str, int]]) -> str:
    """Hash a sorted cell identity set for checkpoint binding."""
    return sha256_json(sorted(cell_keys))


def _cell_key(row: dict[str, Any]) -> tuple[str, str, int]:
    return (str(row["case_id"]), str(row["arm"]), int(row["repeat"]))


def seal_judge_checkpoint(
    *,
    store: AttemptStore,
    scored_rows: list[dict[str, Any]],
    oracle_validation: OracleValidation,
    identity: dict[str, Any],
    spend_evidence: dict[str, Any],
) -> JudgeCheckpoint:
    """Atomically seal judge outputs before the before_verdict gate."""
    answers_path = store.dir / "answers.jsonl"
    if not answers_path.is_file():
        raise CheckpointError("answers.jsonl must be sealed before judge checkpoint")

    scores_rows = [dict(row) for row in scored_rows]
    validation_doc = {
        "canaries_passed": oracle_validation.canaries_passed,
        "repeatability_passed": oracle_validation.repeatability_passed,
        "call_evidence_passed": oracle_validation.call_evidence_passed,
        "details": oracle_validation.details,
    }

    store.write_jsonl("scores.checkpoint.jsonl", scores_rows)
    store.write_json("judge-validation.checkpoint.json", validation_doc)

    answers_hash = sha256_bytes(answers_path.read_bytes())
    scores_hash = sha256_bytes((store.dir / "scores.checkpoint.jsonl").read_bytes())
    validation_hash = sha256_bytes((store.dir / "judge-validation.checkpoint.json").read_bytes())

    cell_keys = sorted(_cell_key(row) for row in scores_rows)
    if len(cell_keys) != len(set(cell_keys)):
        raise CheckpointError("duplicate score rows in checkpoint")

    expected_count = int(identity.get("expected_row_count") or 0)
    if expected_count and len(cell_keys) != expected_count:
        raise CheckpointError("incomplete score row set for checkpoint")

    manifest = {
        "schema_version": 1,
        "identity": identity,
        "answers_hash": answers_hash,
        "scores_checkpoint_hash": scores_hash,
        "judge_validation_checkpoint_hash": validation_hash,
        "expected_cell_set_hash": identity.get("expected_cell_set_hash"),
        "row_count": len(cell_keys),
        "spend_evidence": spend_evidence,
    }
    manifest_hash = sha256_json({key: manifest[key] for key in manifest if key != "manifest_hash"})
    manifest["manifest_hash"] = manifest_hash
    store.write_json("judge-checkpoint.json", manifest)

    return JudgeCheckpoint(
        input_hashes={"answers.jsonl": answers_hash},
        output_hashes={
            "scores.checkpoint.jsonl": scores_hash,
            "judge-validation.checkpoint.json": validation_hash,
            "judge-checkpoint.json": manifest_hash,
        },
        spend_evidence=spend_evidence,
        path=store.dir / "judge-checkpoint.json",
    )


def verify_recoverable_checkpoint(checkpoint_dir: Path) -> VerifiedCheckpoint:
    """Verify sealed checkpoint files and row-set completeness."""
    required = (
        "answers.jsonl",
        "scores.checkpoint.jsonl",
        "judge-validation.checkpoint.json",
        "judge-checkpoint.json",
    )
    for name in required:
        if not (checkpoint_dir / name).is_file():
            raise CheckpointError(f"missing checkpoint artifact: {name}")

    manifest = json.loads((checkpoint_dir / "judge-checkpoint.json").read_text(encoding="utf-8"))
    answers_hash = sha256_bytes((checkpoint_dir / "answers.jsonl").read_bytes())
    scores_hash = sha256_bytes((checkpoint_dir / "scores.checkpoint.jsonl").read_bytes())
    validation_hash = sha256_bytes(
        (checkpoint_dir / "judge-validation.checkpoint.json").read_bytes()
    )

    if manifest.get("answers_hash") != answers_hash:
        raise CheckpointError("checkpoint_hash_mismatch: answers")
    if manifest.get("scores_checkpoint_hash") != scores_hash:
        raise CheckpointError("checkpoint_hash_mismatch: scores")
    if manifest.get("judge_validation_checkpoint_hash") != validation_hash:
        raise CheckpointError("checkpoint_hash_mismatch: judge validation")

    score_rows = read_jsonl(checkpoint_dir / "scores.checkpoint.jsonl")
    cell_keys = [_cell_key(row) for row in score_rows]
    if len(cell_keys) != len(set(cell_keys)):
        raise CheckpointError("row set mismatch: duplicate cell")

    expected_count = int(manifest.get("row_count") or 0)
    if expected_count and len(cell_keys) != expected_count:
        raise CheckpointError("row set mismatch: incomplete")

    identity = manifest.get("identity") or {}
    expected_hash = identity.get("expected_cell_set_hash")
    if expected_hash:
        actual_hash = cell_set_hash(cell_keys)
        if actual_hash != expected_hash:
            raise CheckpointError("checkpoint_row_set_mismatch")

    return VerifiedCheckpoint(recoverable=True, checkpoint_dir=checkpoint_dir)
