"""Business Query sealed-holdout unlock. Do not reuse the SQL experiment loader."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_HOLDOUT_PATH = Path("evals/business_query/v2/cases.holdout.jsonl")
DEFAULT_META_PATH = Path("evals/business_query/v2/holdout.meta.json")
DEFAULT_TRUSTED_ARTIFACT_ROOT = Path("evals/business_query")


class HoldoutAccessDenied(PermissionError):
    """Sealed or regression-only holdout cannot be read."""


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise HoldoutAccessDenied("holdout path is unreadable") from exc


def _canonical_receipt(receipt: dict[str, Any]) -> bytes:
    body = {key: receipt[key] for key in sorted(receipt) if key != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _sign_receipt(receipt: dict[str, Any], signing_key: str) -> str:
    return hmac.new(
        signing_key.encode("utf-8"), _canonical_receipt(receipt), hashlib.sha256
    ).hexdigest()


def _contained(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _require_signing_key(signing_key: str) -> None:
    if not signing_key:
        raise HoldoutAccessDenied("holdout unlock signing key is missing")


def holdout_is_regression_only(meta_path: Path = DEFAULT_META_PATH) -> bool:
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HoldoutAccessDenied("holdout meta is unreadable") from exc
    if not isinstance(payload, dict):
        raise HoldoutAccessDenied("holdout meta is unreadable")
    return payload.get("status") == "regression_only"


def _holdout_gate_applies(cases_path: Path, holdout_path: Path) -> bool:
    try:
        if cases_path.resolve() == holdout_path.resolve():
            return True
    except OSError:
        pass
    if cases_path.exists() and holdout_path.exists():
        return hmac.compare_digest(_hash_file(cases_path), _hash_file(holdout_path))
    return False


def assert_holdout_path_unlocked(
    cases_path: Path,
    *,
    receipt_path: Path | None,
    holdout_path: Path = DEFAULT_HOLDOUT_PATH,
    meta_path: Path = DEFAULT_META_PATH,
    signing_key: str = "",
    trusted_artifact_root: Path = DEFAULT_TRUSTED_ARTIFACT_ROOT,
) -> None:
    """Refuse before reading holdout cases unless a signed, contained receipt exists."""
    if not _holdout_gate_applies(cases_path, holdout_path):
        return
    if holdout_is_regression_only(meta_path):
        raise HoldoutAccessDenied(
            "exposed holdout is regression_only and cannot satisfy the sealed-holdout gate"
        )
    if receipt_path is None or not receipt_path.exists():
        raise HoldoutAccessDenied("sealed holdout requires a hash-linked unlock receipt")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HoldoutAccessDenied("holdout unlock receipt is unreadable") from exc
    if not isinstance(receipt, dict):
        raise HoldoutAccessDenied("holdout unlock receipt is unreadable")
    _require_signing_key(signing_key)
    signature = receipt.get("signature")
    if not isinstance(signature, str) or not signature:
        raise HoldoutAccessDenied("holdout unlock receipt signature is missing")
    expected = _sign_receipt(receipt, signing_key)
    if not hmac.compare_digest(signature, expected):
        raise HoldoutAccessDenied("holdout unlock receipt signature is invalid")
    if receipt.get("status") != "BQ_DEV_PASS":
        raise HoldoutAccessDenied("holdout unlock receipt status is not BQ_DEV_PASS")
    try:
        artifact = Path(receipt["artifact_path"])
    except (KeyError, TypeError) as exc:
        raise HoldoutAccessDenied("holdout unlock artifact path is missing") from exc
    if not _contained(artifact, trusted_artifact_root):
        raise HoldoutAccessDenied("holdout unlock artifact is outside the trusted gate directory")
    if not artifact.exists():
        raise HoldoutAccessDenied("holdout unlock artifact is missing")
    if _hash_file(artifact) != receipt.get("evidence_sha256"):
        raise HoldoutAccessDenied("holdout unlock receipt hash does not match the artifact")
    if receipt.get("evaluator_hash") != receipt.get("bound_evaluator_hash"):
        raise HoldoutAccessDenied("holdout unlock receipt evaluator hash is stale")


def issue_bq_dev_pass(
    gate_result: dict[str, Any],
    *,
    artifact_path: Path,
    receipt_path: Path,
    evaluator_hash: str,
    signing_key: str,
    trusted_artifact_root: Path,
) -> Path:
    _require_signing_key(signing_key)
    if gate_result.get("status") != "BQ_DEV_PASS":
        raise HoldoutAccessDenied("cannot unlock holdout unless the dev verdict is BQ_DEV_PASS")
    if gate_result.get("artifacts_posted") is not True:
        raise HoldoutAccessDenied("partial artifact publication cannot unlock holdout")
    if not _contained(artifact_path, trusted_artifact_root):
        raise HoldoutAccessDenied("holdout unlock artifact is outside the trusted gate directory")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd: int | None = None
    temp_artifact = artifact_path.with_name(artifact_path.name + ".tmp")
    receipt_completed = False
    try:
        fd = os.open(os.fspath(receipt_path), flags)
        payload = (json.dumps(gate_result, sort_keys=True, separators=(",", ":")) + "\n").encode()
        temp_artifact.write_bytes(payload)
        os.replace(temp_artifact, artifact_path)
        receipt = {
            "status": "BQ_DEV_PASS",
            "evidence_sha256": hashlib.sha256(payload).hexdigest(),
            "artifact_path": artifact_path.resolve().as_posix(),
            "evaluator_hash": evaluator_hash,
            "bound_evaluator_hash": evaluator_hash,
            "one_run": True,
        }
        receipt["signature"] = _sign_receipt(receipt, signing_key)
        encoded = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
        os.write(fd, encoded)
        os.fsync(fd)
        os.close(fd)
        fd = None
        receipt_completed = True
    except FileExistsError as exc:
        raise HoldoutAccessDenied(
            "second-run unlock is refused; one-run receipt already exists"
        ) from exc
    except HoldoutAccessDenied:
        raise
    except OSError as exc:
        created_stub = fd is not None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
        if created_stub:
            receipt_path.unlink(missing_ok=True)
        temp_artifact.unlink(missing_ok=True)
        if not receipt_completed:
            try:
                if artifact_path.is_file():
                    artifact_path.unlink()
            except OSError:
                pass
        raise HoldoutAccessDenied("holdout unlock artifact could not be written") from exc
    return receipt_path
