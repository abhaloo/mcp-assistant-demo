"""Frozen experiment manifest verification for DeepSeek routing evals."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.paths import REPO_ROOT
from app.pricing.prices import require_price_key

EXPERIMENT_ID = "deepseek-v4-flash-0731"
EXPERIMENT_DIR = REPO_ROOT / "evals" / "experiments" / EXPERIMENT_ID
MANIFEST_PATH = EXPERIMENT_DIR / "manifest.json"
RAG_CASE_IDS_PATH = EXPERIMENT_DIR / "rag_case_ids.json"
SQL_DEV_PATH = REPO_ROOT / "evals" / "sql" / "cases.jsonl"
HOLDOUT_PATH = EXPERIMENT_DIR / "sealed" / "cases_holdout.jsonl"
DEEPSEEK_MODEL = "deepseek/deepseek-v4-flash-0731"
# Canonical response id from /generation metadata (not the request alias).
DEEPSEEK_ACTUAL_MODELS = ("deepseek/deepseek-v4-flash-20260731",)
# Empirical DeepInfra output cap for deepseek-v4-flash-0731.
DEEPINFRA_DEEPSEEK_EFFECTIVE_OUTPUT_CAP = 65_536


class ManifestDriftError(ValueError):
    """Raised when frozen experiment artifacts drift from the checked-in manifest."""


def sha256_file(path: Path) -> str:
    """Hash file bytes with CRLF normalized to LF.

    Manifest pins match git blobs / Linux checkouts (LF). Windows ``text=auto``
    working trees may present CRLF; without normalization, verify_manifest passes
    locally and fails in cloud (or the reverse).
    """
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def sha256_rag_case_ids(case_ids: list[str]) -> str:
    canonical = json.dumps(sorted(case_ids), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def sha256_frozen_rag_cases(dataset_path: Path, case_ids: list[str]) -> str:
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in data if isinstance(c, dict) and "id" in c}
    missing = sorted(set(case_ids) - set(by_id))
    if missing:
        msg = f"rag eval dataset is missing frozen case ids: {missing}"
        raise ManifestDriftError(msg)
    rows = [by_id[cid] for cid in sorted(case_ids)]
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    manifest_path = path or MANIFEST_PATH
    if not manifest_path.is_file():
        msg = f"experiment manifest missing: {manifest_path}"
        raise ManifestDriftError(msg)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _assert_settings_defaults(manifest: dict[str, Any]) -> None:
    defaults = manifest["defaults"]
    settings = Settings()
    for key, expected in defaults.items():
        if hasattr(settings, key):
            actual = getattr(settings, key)
            if actual != expected:
                msg = f"{key} default drift: expected {expected!r}, got {actual!r}"
                raise ManifestDriftError(msg)


def verify_manifest(
    manifest: dict[str, Any] | None = None,
    *,
    repo_root: Path | None = None,
    rag_case_ids_path: Path | None = None,
    sql_dev_path: Path | None = None,
    holdout_path: Path | None = None,
    prices_path: Path | None = None,
    eval_dataset_path: Path | None = None,
) -> dict[str, Any]:
    """Verify frozen artifacts match *manifest*; raise on any drift."""
    root = repo_root or REPO_ROOT
    manifest_doc = manifest or load_manifest(root / MANIFEST_PATH.relative_to(REPO_ROOT))

    rag_path = rag_case_ids_path or (root / manifest_doc["rag"]["case_ids_path"])
    dev_path = sql_dev_path or (root / manifest_doc["sql_dev"]["path"])
    sealed_path = holdout_path or (root / manifest_doc["sql_holdout"]["path"])
    prices_file = prices_path or (root / "config" / "prices.yaml")

    if not rag_path.is_file():
        msg = f"rag case IDs missing: {rag_path}"
        raise ManifestDriftError(msg)
    if not dev_path.is_file():
        msg = f"sql dev cases missing: {dev_path}"
        raise ManifestDriftError(msg)
    if not sealed_path.is_file():
        msg = f"sql holdout missing: {sealed_path}"
        raise ManifestDriftError(msg)
    if not prices_file.is_file():
        msg = f"prices.yaml missing: {prices_file}"
        raise ManifestDriftError(msg)

    rag_ids = json.loads(rag_path.read_text(encoding="utf-8"))
    if len(rag_ids) != manifest_doc["rag"]["case_count"]:
        msg = (
            "rag case count drift: "
            f"expected {manifest_doc['rag']['case_count']}, got {len(rag_ids)}"
        )
        raise ManifestDriftError(msg)
    if sha256_rag_case_ids(rag_ids) != manifest_doc["rag"]["case_ids_sha256"]:
        msg = "rag case IDs hash drift"
        raise ManifestDriftError(msg)

    dataset_path = eval_dataset_path or (root / manifest_doc["rag"]["eval_dataset_path"])
    if not dataset_path.is_file():
        msg = f"rag eval dataset missing: {dataset_path}"
        raise ManifestDriftError(msg)
    if sha256_frozen_rag_cases(dataset_path, rag_ids) != manifest_doc["rag"]["eval_dataset_sha256"]:
        msg = "rag eval dataset hash drift"
        raise ManifestDriftError(msg)

    _PINNED = (
        ("sql_dev", dev_path, "sql dev"),
        ("sql_holdout", sealed_path, "sql holdout"),
    )
    for section, pth, label in _PINNED:
        lines = [line for line in pth.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) != manifest_doc[section]["case_count"]:
            msg = (
                f"{label} case count drift: "
                f"expected {manifest_doc[section]['case_count']}, got {len(lines)}"
            )
            raise ManifestDriftError(msg)
        if sha256_file(pth) != manifest_doc[section]["sha256"]:
            msg = f"{label} file hash drift"
            raise ManifestDriftError(msg)

    if sha256_file(prices_file) != manifest_doc["prices_yaml_sha256"]:
        msg = "prices.yaml hash drift"
        raise ManifestDriftError(msg)

    require_price_key(manifest_doc["defaults"]["experiment_model"], prices_path=prices_file)
    _assert_settings_defaults(manifest_doc)
    return manifest_doc
