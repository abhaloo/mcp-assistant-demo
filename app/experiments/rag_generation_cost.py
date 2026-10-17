"""OpenRouter generation metadata validation and answer cost derivation."""

from __future__ import annotations

import math
from typing import Any


class RagGenerationCostError(RuntimeError):
    """Generation metadata or cost evidence is invalid."""


_REQUIRED_GENERATION_FIELDS = ("generation_id", "provider_name", "model")


def require_generation_fields(route_metadata: dict[str, Any]) -> None:
    """Fail closed when OpenRouter generation metadata omits required fields."""
    missing = [field for field in _REQUIRED_GENERATION_FIELDS if not route_metadata.get(field)]
    if missing:
        raise RagGenerationCostError(
            f"generation metadata omitted required fields: {', '.join(missing)}"
        )


def derive_cost_usd(
    *,
    provider_reported_cost: Any,
    estimated_cost_usd: float,
) -> tuple[float, float | None]:
    """Return (cost_usd, provider_reported_cost_usd) from provider or estimate."""
    try:
        provider_reported_cost_usd = float(provider_reported_cost)
    except (TypeError, ValueError):
        provider_reported_cost_usd = None
    if provider_reported_cost_usd is not None and (
        not math.isfinite(provider_reported_cost_usd) or provider_reported_cost_usd < 0
    ):
        provider_reported_cost_usd = None
    cost_usd = (
        provider_reported_cost_usd if provider_reported_cost_usd is not None else estimated_cost_usd
    )
    if not math.isfinite(cost_usd) or cost_usd < 0:
        raise RagGenerationCostError("derived answer cost is not finite and non-negative")
    return cost_usd, provider_reported_cost_usd
