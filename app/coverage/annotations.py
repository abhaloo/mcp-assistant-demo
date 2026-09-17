"""Human findings kept beside the generated map, keyed by table.column or resource/view."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from app.coverage.model import ColumnBinding, CoverageMap

if TYPE_CHECKING:
    from app.coverage.schema_inventory import SchemaInventory


class DuplicateAnnotationKeyError(ValueError):
    """Two annotations share one key."""


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Preserve order and reject duplicate keys."""
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise DuplicateAnnotationKeyError(key)
        out[key] = value
    return out


def load_annotations(path: Path) -> dict[str, str]:
    """Load annotations from a JSON file and fail closed on duplicate keys."""
    raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
    return {str(k): str(v) for k, v in raw.items()}


def merge(
    coverage_map: CoverageMap,
    annotations: dict[str, str],
    inventory: SchemaInventory | None = None,
) -> CoverageMap:
    """Attach annotations to a coverage map and record stale keys.

    Identifies valid column keys from column bindings and inventory tables.
    Identifies valid view keys from resource views and wildcard resource keys.
    Keys not present in valid columns or views are recorded in stale_annotations.
    """
    columns = {
        f"{b.table}.{b.column}"
        for r in coverage_map.rows
        for b in r.bindings
        if isinstance(b, ColumnBinding)
    }
    if inventory is not None:
        columns |= {f"{t}.{c.column}" for t, cols in inventory.tables.items() for c in cols}
    views = {f"{r.resource}/{r.view}" for r in coverage_map.rows if r.view} | {
        f"{r.resource}/*" for r in coverage_map.rows
    }
    stale = sorted(k for k in annotations if k not in columns and k not in views)
    return coverage_map.model_copy(
        update={"annotations": dict(annotations), "stale_annotations": stale}
    )
