"""Materialized eval snapshot readiness receipt."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

from app.eval.sql.snapshot import gold_verification_receipt

__all__ = [
    "PINNED_DUMP_CONTENT_SHA256",
    "PINNED_DUMP_GIT_BLOB_OID",
    "SnapshotReceipt",
    "SnapshotReceiptError",
    "SnapshotVerifyConfig",
    "verify_materialized_snapshot",
]

PINNED_DUMP_GIT_BLOB_OID = "bc7491259ffd5a6698357d9aefc5fdb2f324d9a8"
PINNED_DUMP_CONTENT_SHA256 = "64bf2d927a6bde5a202b2a8121bda4dd99ba78c1c9a905b5eb43d97d723c4327"
_REQUIRED_DB_NAME = "mcp_local"
_PROD_FORWARD_MARKERS = ("prod-billing", "prod-forward", "billing-tunnel", "production")


class SnapshotReceiptError(RuntimeError):
    """Snapshot is not materialized, safe, or hash-bound."""


@dataclass(frozen=True)
class SnapshotVerifyConfig:
    eval_snapshot_url: str
    eval_today: str
    expected_git_blob_oid: str = PINNED_DUMP_GIT_BLOB_OID
    expected_content_sha256: str = PINNED_DUMP_CONTENT_SHA256
    materialized: bool = True
    prod_forward: bool = False
    sidecar_container_id: str | None = None
    import_watermark_hash: str | None = None
    dump_bytes_sha256: str | None = None
    cases: tuple[dict[str, Any], ...] = ()

    def replace(self, **changes: Any) -> SnapshotVerifyConfig:
        return SnapshotVerifyConfig(**{**asdict(self), **changes})


@dataclass(frozen=True)
class SnapshotReceipt:
    host_class: str
    host: str
    sidecar_container_id: str
    import_watermark_hash: str
    dump_git_blob_oid: str
    dump_content_sha256: str
    read_only_write_refused: bool
    schema_hash: str
    gold_hash: str
    eval_today: str
    eval_snapshot_url_sha256: str
    receipt_hash: str

    def model_dump_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _refuse_url(url: str, *, prod_forward: bool) -> None:
    lowered = url.lower()
    if any(marker in lowered for marker in _PROD_FORWARD_MARKERS):
        raise SnapshotReceiptError("production or tunnel snapshot URL refused")
    if prod_forward:
        raise SnapshotReceiptError("prod-forward localhost snapshot refused")
    parsed = urlparse(url)
    database = (parsed.path or "").lstrip("/")
    if database != _REQUIRED_DB_NAME:
        raise SnapshotReceiptError(f"database path must be {_REQUIRED_DB_NAME!r}")


def verify_materialized_snapshot(
    config: SnapshotVerifyConfig,
    *,
    connect_fn: Callable[[SnapshotVerifyConfig], dict[str, Any]],
    gold_verification_fn: Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]] | None = None,
) -> SnapshotReceipt:
    """Verify a materialized sidecar snapshot before any SQL LLM spend."""
    if not config.materialized:
        raise SnapshotReceiptError("unmaterialized localhost snapshot refused")
    if not config.eval_snapshot_url:
        raise SnapshotReceiptError("eval snapshot URL is required")
    _refuse_url(config.eval_snapshot_url, prod_forward=config.prod_forward)
    parsed = urlparse(config.eval_snapshot_url)
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost"}:
        raise SnapshotReceiptError("snapshot host must be localhost sidecar")

    if config.dump_bytes_sha256 and config.dump_bytes_sha256 != config.expected_content_sha256:
        raise SnapshotReceiptError("content sha256 mismatch for materialized dump bytes")
    if config.expected_git_blob_oid != PINNED_DUMP_GIT_BLOB_OID:
        raise SnapshotReceiptError("git blob oid mismatch")
    if config.expected_content_sha256 != PINNED_DUMP_CONTENT_SHA256:
        raise SnapshotReceiptError("content sha256 pin mismatch")

    if not config.sidecar_container_id or not config.import_watermark_hash:
        raise SnapshotReceiptError(
            "host_class requires sidecar container and import watermark proof"
        )

    probe = connect_fn(config)
    if not probe.get("read_only_write_refused"):
        raise SnapshotReceiptError("read-only write probe did not refuse mutation")
    schema_hash = str(probe.get("schema_hash") or "")
    if not schema_hash.startswith("sha256:"):
        raise SnapshotReceiptError("schema_hash missing from sidecar proof")

    gold_fn = gold_verification_fn or gold_verification_receipt
    gold = gold_fn(list(config.cases)) if config.cases else {}
    gold_hash = _hash_payload({"gold_verification": gold})
    url_digest = hashlib.sha256(config.eval_snapshot_url.encode()).hexdigest()
    identity = {
        "eval_snapshot_url_sha256": f"sha256:{url_digest}",
        "eval_today": config.eval_today,
    }
    body = {
        "host_class": "ephemeral_sidecar",
        "host": host,
        "sidecar_container_id": config.sidecar_container_id,
        "import_watermark_hash": config.import_watermark_hash,
        "dump_git_blob_oid": config.expected_git_blob_oid,
        "dump_content_sha256": config.expected_content_sha256,
        "read_only_write_refused": True,
        "schema_hash": schema_hash,
        "gold_hash": gold_hash,
        "eval_today": config.eval_today,
        "eval_snapshot_url_sha256": identity["eval_snapshot_url_sha256"],
    }
    receipt_hash = _hash_payload(body)
    payload_json = json.dumps({**body, "receipt_hash": receipt_hash}, sort_keys=True)
    if re.search(r"password|mysql\+pymysql://[^@]+@", payload_json, re.I):
        raise SnapshotReceiptError("receipt must not contain credential plaintext")
    return SnapshotReceipt(receipt_hash=receipt_hash, **body)
