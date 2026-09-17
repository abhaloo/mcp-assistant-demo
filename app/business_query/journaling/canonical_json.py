from __future__ import annotations

import json
from typing import Any


def canonical_json_str(payload: Any) -> str:
    """Return deterministic JSON text for a supported payload."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def canonical_json_bytes(payload: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a supported payload."""
    return canonical_json_str(payload).encode("utf-8")
