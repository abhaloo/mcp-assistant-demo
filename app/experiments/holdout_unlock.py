"""Machine-checked SQL-dev gate receipt for holdout unlock.

Holdout cases under ``sealed/`` must only be loaded via ``load_holdout_cases()``
after a receipt whose ``evidence_sha256`` matches a persisted gate artifact.
Hand-minted receipts (arbitrary hashes / SQL-ELIGIBLE status) do not unlock.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.experiments.core.hashing import sha256_bytes, sha256_file
from app.experiments.core.jsonl import read_jsonl

EXPERIMENT_DIR = Path("evals/experiments/deepseek-v4-flash-0731")
GATES_DIR = EXPERIMENT_DIR / "gates"
RECEIPT_PATH = GATES_DIR / "sql_dev_pass.json"
ARTIFACT_PATH = GATES_DIR / "sql_dev_gate_result.json"
SEALED_HOLDOUT_PATH = EXPERIMENT_DIR / "sealed" / "cases_holdout.jsonl"

_ALLOWED_RECEIPT_STATUS = "SQL_DEV_PASS"


def hash_bytes(data: bytes) -> str:
    return sha256_bytes(data, prefixed=False)


def hash_file(path: Path) -> str:
    return sha256_file(path, prefixed=False)


def issue_sql_dev_pass(
    gate_result: dict[str, Any],
    *,
    artifact_path: Path | None = None,
    receipt_path: Path | None = None,
) -> Path:
    """Persist gate artifact + receipt. Only ``SQL_DEV_PASS`` unlocks holdout."""
    status = gate_result.get("status")
    if status != _ALLOWED_RECEIPT_STATUS:
        raise PermissionError(
            f"cannot unlock holdout for gate status {status!r}; need {_ALLOWED_RECEIPT_STATUS}"
        )
    details = gate_result.get("details")
    if not isinstance(details, dict) or not details:
        raise PermissionError("SQL_DEV_PASS artifact requires non-empty details")
    stages = details.get("stages")
    if not isinstance(stages, dict) or stages.get("smoke_passed") is not True:
        raise PermissionError("SQL_DEV_PASS artifact requires stages.smoke_passed=true")
    pass_rate = details.get("pass_rate")
    if not isinstance(pass_rate, dict) or int(pass_rate.get("n") or 0) < 1:
        raise PermissionError("SQL_DEV_PASS artifact requires pass_rate.n >= 1")

    art = ARTIFACT_PATH if artifact_path is None else artifact_path
    rec = RECEIPT_PATH if receipt_path is None else receipt_path
    art.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(gate_result, sort_keys=True, separators=(",", ":"))
    artifact_bytes = (payload + "\n").encode("utf-8")
    art.write_bytes(artifact_bytes)
    evidence = hash_bytes(artifact_bytes)

    receipt = {
        "status": _ALLOWED_RECEIPT_STATUS,
        "evidence_sha256": evidence,
        "artifact_path": art.as_posix(),
    }
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return rec


def is_holdout_unlocked(
    *,
    receipt_path: Path | None = None,
    artifact_path: Path | None = None,
) -> bool:
    rec_path = RECEIPT_PATH if receipt_path is None else receipt_path
    art_path = ARTIFACT_PATH if artifact_path is None else artifact_path
    if not rec_path.is_file() or not art_path.is_file():
        return False
    try:
        data = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if data.get("status") != _ALLOWED_RECEIPT_STATUS:
        return False
    expected = data.get("evidence_sha256") or ""
    if len(expected) < 32:
        return False
    actual = hash_bytes(art_path.read_bytes())
    return actual == expected


def assert_holdout_unlocked(
    *,
    receipt_path: Path | None = None,
    artifact_path: Path | None = None,
) -> None:
    if not is_holdout_unlocked(receipt_path=receipt_path, artifact_path=artifact_path):
        path = RECEIPT_PATH if receipt_path is None else receipt_path
        raise PermissionError(
            "holdout evaluation refused: missing or invalid sql_dev gate receipt "
            f"at {path.as_posix()} (artifact hash must match)"
        )


def sealed_holdout_path(*, receipt_path: Path | None = None) -> Path:
    """Return sealed holdout path only after unlock receipt verifies."""
    assert_holdout_unlocked(receipt_path=receipt_path)
    if not SEALED_HOLDOUT_PATH.is_file():
        raise FileNotFoundError(f"sealed holdout missing: {SEALED_HOLDOUT_PATH}")
    return SEALED_HOLDOUT_PATH


def load_holdout_cases(*, receipt_path: Path | None = None) -> list[dict[str, Any]]:
    """Sole public loader for holdout cases — bypass via raw path is out of contract."""
    return read_jsonl(sealed_holdout_path(receipt_path=receipt_path))
