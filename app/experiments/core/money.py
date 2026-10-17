"""Money arithmetic and token cost estimation for experiments."""

from __future__ import annotations

import math
from pathlib import Path

from app.experiments.core.errors import RagExperimentError
from app.pricing.prices import PRICES_PATH, load_prices, require_price_key

BUDGET_EPSILON_USD = 1e-9
RESERVATION_DECIMALS = 6

__all__ = [
    "BUDGET_EPSILON_USD",
    "RESERVATION_DECIMALS",
    "estimate_cost_usd",
    "require_finite_nonnegative_usd",
    "require_finite_positive_usd",
    "round_reservation_usd",
]


def round_reservation_usd(value: float) -> float:
    """Round a finite positive reservation upward to the ledger's money precision."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError("reservation must be finite and positive")
    scale = 10**RESERVATION_DECIMALS
    return math.ceil(value * scale) / scale


def require_finite_nonnegative_usd(value: float, *, what: str) -> float:
    """A charge may be zero but never negative, NaN, or infinite.

    NaN evades every bare `>` comparison downstream and a negative charge does
    not merely dodge a ceiling -- it buys headroom under the campaign cap.
    """
    if not math.isfinite(value) or value < 0:
        raise RagExperimentError(f"{what} must be finite and non-negative, got {value!r}")
    return value


def require_finite_positive_usd(value: float, *, what: str) -> float:
    """A forecast must be strictly positive: reserving against zero reserves nothing."""
    if not math.isfinite(value) or value <= 0:
        raise RagExperimentError(f"{what} must be finite and positive, got {value!r}")
    return value


def estimate_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    *,
    prices_path: Path | None = None,
) -> float:
    """Estimate USD from prices.yaml rates (per 1k tokens)."""
    path = prices_path or PRICES_PATH
    price_key = require_price_key(model_id, prices_path=path)
    row = load_prices(path)[price_key]
    return (input_tokens / 1000.0) * row["input_per_1k"] + (output_tokens / 1000.0) * row[
        "output_per_1k"
    ]
