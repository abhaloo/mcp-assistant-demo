"""Finalize verified campaign checkpoints without paid replay."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.experiments.core.checkpoint import verify_recoverable_checkpoint
from app.experiments.core.errors import RagExperimentError
from app.experiments.core.jsonl import read_jsonl
from app.experiments.core.ledger_view import LedgerView
from app.experiments.core.runtime_identity import RuntimeIdentity
from app.experiments.core.spend_reconciliation import reconcile_spend_evidence
from app.experiments.core.stage_receipts import write_stage_receipt

__all__ = [
    "RecoveryError",
    "finalize_from_checkpoint",
]


class RecoveryError(RagExperimentError):
    """Recovery refused before writing verdict artifacts."""


@dataclass(frozen=True)
class VerifiedCheckpointContext:
    attempt_dir: Path
    checkpoint: Any


def _verify_allowlisted_runtime(
    *,
    frozen_runtime: dict[str, Any],
    current: RuntimeIdentity,
    allowlist: list[str],
) -> None:
    frozen_commit = str(frozen_runtime.get("commit_sha") or "")
    if "commit_sha" in allowlist and current.commit_sha != frozen_commit:
        raise RecoveryError("commit_sha mismatch")
    if "dirty" in allowlist and current.dirty:
        raise RecoveryError("dirty worktree")


def finalize_from_checkpoint(
    *,
    task: Any,
    frozen: Any,
    attempt_dir: Path,
    current_runtime: RuntimeIdentity,
    ledger_path: Path,
    contract: dict[str, Any],
    ledger: Any | None = None,
) -> Any:
    """Finalize from a sealed judge checkpoint without answer or judge replay."""
    if (attempt_dir / "verdict.json").exists():
        raise RecoveryError("verdict already exists")

    verified = verify_recoverable_checkpoint(attempt_dir)
    if not verified.recoverable:
        raise RecoveryError("checkpoint not recoverable")

    allowlist = list(contract["recovery_identity_allowlist"])
    frozen_runtime = getattr(frozen, "document", {}).get("runtime") or getattr(
        frozen, "runtime", {}
    )
    _verify_allowlisted_runtime(
        frozen_runtime=frozen_runtime,
        current=current_runtime,
        allowlist=allowlist,
    )

    ledger_obj = ledger if ledger is not None else _SimpleLedger(ledger_path)
    reconciliation = reconcile_spend_evidence(
        ledger=LedgerView.from_any(ledger_obj),
        heartbeat=_read_json_if_exists(attempt_dir / "heartbeat.json"),
        staged=_read_json_if_exists(attempt_dir / "attempt.json"),
    )
    if reconciliation.status == "UNSETTLED":
        raise RecoveryError("spend_unsettled")

    task.verify_frozen_contract()
    scored_rows = read_jsonl(attempt_dir / "scores.checkpoint.jsonl")
    validation_doc = _read_json(attempt_dir / "judge-validation.checkpoint.json")
    from app.experiments.core.protocol import OracleValidation

    oracle_validation = OracleValidation(
        canaries_passed=bool(validation_doc.get("canaries_passed")),
        repeatability_passed=bool(validation_doc.get("repeatability_passed")),
        call_evidence_passed=bool(validation_doc.get("call_evidence_passed", True)),
        details=dict(validation_doc.get("details") or {}),
    )

    from app.experiments.core.attempt_store import AttemptStore

    store = AttemptStore.open_existing(attempt_dir)

    verdict = task.finalize(
        scored_rows=scored_rows,
        oracle_validation=oracle_validation,
        actual_spend_usd=reconciliation.authoritative_usd,
        store=store,
    )
    write_stage_receipt(
        store=store,
        stage="verdict_sealed",
        inputs={"judge-checkpoint.json": "verified"},
        outputs={"verdict.json": "written"},
        spend={"reconciliation": reconciliation.status},
    )
    return verdict


class _SimpleLedger:
    def __init__(self, path: Path) -> None:
        self._path = path
        self.settled_usd = 0.0
        self.has_open_reservation = False
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.settled_usd = float(payload.get("settled_usd") or 0.0)
            self.has_open_reservation = bool(payload.get("open_reservation"))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return _read_json(path)
