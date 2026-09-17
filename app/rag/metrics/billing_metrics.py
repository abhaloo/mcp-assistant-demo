"""Load the checked-in billing metric definition catalog."""

from __future__ import annotations

from pathlib import Path

import yaml

_DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent / "billing_metrics.yaml"


def load_catalog(path: str | Path | None = None) -> list[dict]:
    """Return metric definition dicts from the YAML catalog."""
    catalog_path = Path(path) if path is not None else _DEFAULT_CATALOG_PATH
    with catalog_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Expected mapping at catalog root, got {type(raw).__name__}")
    metrics = raw.get("metrics")
    if not isinstance(metrics, list):
        raise ValueError("Catalog must contain a top-level 'metrics' list")
    return metrics
