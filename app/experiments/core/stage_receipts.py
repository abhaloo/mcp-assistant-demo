"""Hash-linked campaign stage and terminal receipts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.experiments.core.atomic_io import atomic_write_text_exclusive
from app.experiments.core.attempt_store import AttemptStore

__all__ = [
    "ALLOWED_RECEIPT_STAGES",
    "StageReceipt",
    "TerminalState",
    "derive_terminal_state",
    "write_stage_receipt",
]

ALLOWED_RECEIPT_STAGES = frozenset(
    {
        "freeze_sealed",
        "canary_sealed",
        "answers_sealed",
        "judge_checkpoint_sealed",
        "verdict_sealed",
        "termination",
        "artifact_export",
    }
)


@dataclass(frozen=True)
class StageReceipt:
    stage: str
    receipt_hash: str
    path: Path


@dataclass(frozen=True)
class TerminalState:
    name: str
    required_action: str | None = None


def _receipts_dir(store: AttemptStore) -> Path:
    return store.dir / "receipts"


def _previous_receipt_hash(store: AttemptStore) -> str | None:
    receipts_dir = _receipts_dir(store)
    if not receipts_dir.is_dir():
        return None
    hashes: list[tuple[str, str]] = []
    for path in receipts_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        written_at = str(payload.get("written_at") or "")
        receipt_hash = str(payload.get("receipt_hash") or "")
        if receipt_hash:
            hashes.append((written_at, receipt_hash))
    if not hashes:
        return None
    hashes.sort(key=lambda item: item[0])
    return hashes[-1][1]


def _hash_receipt_body(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def write_stage_receipt(
    *,
    store: AttemptStore,
    stage: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    spend: dict[str, Any],
    terminal_state: str | None = None,
    required_action: str | None = None,
) -> StageReceipt:
    """Seal one stage receipt; never overwrites an existing receipt for the stage."""
    if stage not in ALLOWED_RECEIPT_STAGES:
        raise ValueError(f"unsupported receipt stage: {stage}")

    written_at = datetime.now(tz=UTC).isoformat()
    body_without_hash: dict[str, Any] = {
        "schema_version": 1,
        "written_at": written_at,
        "stage": stage,
        "previous_receipt_hash": _previous_receipt_hash(store),
        "inputs": inputs,
        "outputs": outputs,
        "spend": spend,
    }
    if terminal_state is not None:
        body_without_hash["terminal_state"] = terminal_state
    if required_action is not None:
        body_without_hash["required_action"] = required_action

    receipt_hash = _hash_receipt_body(body_without_hash)
    payload = {**body_without_hash, "receipt_hash": receipt_hash}

    receipts_dir = _receipts_dir(store)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    path = receipts_dir / f"{stage}.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text_exclusive(path, text)
    return StageReceipt(stage=stage, receipt_hash=receipt_hash, path=path)


def _load_receipt(attempt_dir: Path, stage: str) -> dict[str, Any] | None:
    path = attempt_dir / "receipts" / f"{stage}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def derive_terminal_state(attempt_dir: Path) -> TerminalState:
    """Derive terminal evidence state from sealed receipts and artifacts."""
    verdict_receipt = _load_receipt(attempt_dir, "verdict_sealed")
    termination_receipt = _load_receipt(attempt_dir, "termination")
    status_warning = (attempt_dir / "status-warning.json").is_file()

    if verdict_receipt is not None:
        if status_warning:
            return TerminalState(name="COMPLETE_WITH_STATUS_WARNING")
        return TerminalState(name="VERDICT_COMPLETE")

    if termination_receipt is not None:
        terminal_name = str(termination_receipt.get("terminal_state") or "ABORTED_NO_RECOVERY")
        required_action = termination_receipt.get("required_action")
        return TerminalState(
            name=terminal_name,
            required_action=str(required_action) if required_action else None,
        )

    checkpoint_receipt = _load_receipt(attempt_dir, "judge_checkpoint_sealed")
    if checkpoint_receipt is not None and (attempt_dir / "judge-checkpoint.json").is_file():
        return TerminalState(
            name="RECOVERABLE_CHECKPOINT",
            required_action="finalize-from-checkpoint",
        )

    return TerminalState(name="ABORTED_NO_RECOVERY")
