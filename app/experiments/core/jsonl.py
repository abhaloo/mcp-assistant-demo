"""Canonical JSONL reading helper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["read_jsonl"]


def read_jsonl(path: Path | str) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dictionaries, ignoring empty lines."""
    p = Path(path)
    if not p.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
