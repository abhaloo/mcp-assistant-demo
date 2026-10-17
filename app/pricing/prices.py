"""Frozen price-table loading — the production home for token pricing data.

Pricing is a production concern (every answer path prices tokens via
`app.pricing.pricer`), not a control-plane one. `app.experiments.manifest`
re-exports these three names for its own (control-plane) consumers so their
imports keep working unchanged.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.process_state import register_resettable

REPO_ROOT = Path(__file__).resolve().parents[2]
PRICES_PATH = REPO_ROOT / "config" / "prices.yaml"


@lru_cache(maxsize=4)
def load_prices(prices_path: Path) -> dict[str, Any]:
    return yaml.safe_load(prices_path.read_text(encoding="utf-8"))


register_resettable(load_prices.cache_clear)


def require_price_key(
    model_id: str,
    *,
    prices: dict[str, Any] | None = None,
    prices_path: Path | None = None,
) -> str:
    """Return the prices.yaml key for *model_id* or raise if unpriced."""
    doc = prices if prices is not None else load_prices(prices_path or PRICES_PATH)
    alias_map = doc.get("openrouter_model_to_price_key", {}) or {}
    azure_map = doc.get("azure_deployment_to_price_key", {}) or {}
    price_key = alias_map.get(model_id, azure_map.get(model_id, model_id))
    price_rows = {k: v for k, v in doc.items() if not k.endswith("_to_price_key")}
    if price_key not in price_rows:
        msg = f"no price for model {model_id!r} (key {price_key!r}) — add it to prices.yaml"
        raise KeyError(msg)
    return price_key
