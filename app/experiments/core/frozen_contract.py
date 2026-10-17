"""Re-read a frozen experiment contract from disk and re-derive its hash."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol


class _FrozenExperiment(Protocol):
    output_path: Any
    document: dict[str, Any]
    freeze_hash: str


def verify_frozen_contract(frozen: _FrozenExperiment, *, error_type: type[Exception]) -> None:
    """Re-read the frozen contract from disk and re-derive its hash."""
    if not frozen.output_path.is_file():
        raise error_type("frozen contract file is missing")
    try:
        disk_document = json.loads(frozen.output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise error_type("frozen contract file is unreadable") from exc
    if disk_document != frozen.document:
        raise error_type("frozen contract file differs from the bound snapshot")
    document_without_hash = {
        key: value for key, value in frozen.document.items() if key != "freeze_hash"
    }
    payload = json.dumps(
        document_without_hash,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    actual_hash = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if actual_hash != frozen.freeze_hash:
        raise error_type("frozen contract hash mismatch")
