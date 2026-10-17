"""SHA-256 identity for pinned eval manifests."""

from __future__ import annotations

import hashlib
from pathlib import Path


def manifest_hash(path: str | Path) -> str:
    """Return the SHA-256 hex digest of a manifest file at ``path``."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()
