"""Plan shapes Cube may execute live: those whose Cube and internal results agreed."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AgreedShapes = frozenset[str]


def load_agreed_shapes(path: Path | None) -> AgreedShapes:
    """Empty when unset or unreadable. A shape needs full agreement, including every
    time-typed column, before Cube may answer it for a person."""
    if path is None or not path.exists():
        return frozenset()
    payload = json.loads(path.read_text(encoding="utf-8"))
    agreed: set[str] = set()
    for shape in payload.get("shapes", []):
        key = shape.get("key")
        if not key or not shape.get("agreed"):
            continue
        columns = shape.get("columns", {})
        if not columns or any(ok is not True for ok in columns.values()):
            logger.info(
                "cube shape excluded: missing or disagreeing column evidence",
                extra={"shape": key},
            )
            continue
        if shape.get("requires_time_evidence") and int(shape.get("time_columns_agreed", 0)) < 1:
            logger.info("cube shape excluded: no agreed time column", extra={"shape": key})
            continue
        agreed.add(key)
    return frozenset(agreed)
