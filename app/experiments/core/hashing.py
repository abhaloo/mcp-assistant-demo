"""Canonical SHA-256 hashing helpers for experiment artifacts and payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = ["sha256_bytes", "sha256_file", "sha256_json"]


def sha256_bytes(payload: bytes, *, prefixed: bool = True) -> str:
    """Hex digest, `sha256:`-prefixed by default.

    `prefixed=False` is the holdout receipt's format: its already-written
    receipts carry the bare digest and are compared against it by a later run.
    """
    digest = hashlib.sha256(payload).hexdigest()
    return f"sha256:{digest}" if prefixed else digest


def sha256_file(path: Path | str, *, prefixed: bool = True) -> str:
    """Compute the SHA-256 digest of a file."""
    p = Path(path)
    digest = hashlib.sha256()
    with p.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    hexdigest = digest.hexdigest()
    return f"sha256:{hexdigest}" if prefixed else hexdigest


def sha256_json(obj: Any, *, prefixed: bool = True) -> str:
    """Compute the SHA-256 digest of a JSON-serializable object with canonical formatting."""
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(canonical.encode("utf-8"), prefixed=prefixed)
