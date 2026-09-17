"""Fail-closed token pricing from the frozen price table."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.pricing.prices import PRICES_PATH, load_prices, require_price_key

_USD_QUANT = Decimal("0.0000001")


@dataclass(frozen=True)
class PricedTokens:
    estimated_usd: Decimal | None
    cost_status: str
    price_table_version: str | None


def _price_table_version(prices_doc: dict[str, Any]) -> str | None:
    version = prices_doc.get("version")
    if version is None:
        return None
    text = str(version).strip()
    return text or None


def _per_1k_cost(tokens: int, rate: object) -> Decimal:
    return (Decimal(tokens) / Decimal(1000)) * Decimal(str(rate))


def _output_leg(
    output_tokens: int,
    reasoning_tokens: int | None,
    output_rate: object,
    reasoning_rate: object | None,
) -> tuple[Decimal, str]:
    """Price the output leg, treating reasoning as a SUBSET of output tokens.

    Providers report reasoning inside ``output_tokens`` -- a live call reported
    input + output equal to the reported total exactly, leaving the reasoning
    count no room outside output. So reasoning is never a separate leg added on
    top; it only ever re-prices a slice of output that the table values at a
    different rate.

    "partial" means the table prices reasoning separately but the provider did
    not report how much of the output was reasoning, so the split -- and the
    total -- could be off in either direction.
    """
    if reasoning_tokens is None:
        status = "partial" if reasoning_rate is not None else "complete"
        return _per_1k_cost(output_tokens, output_rate), status
    if reasoning_tokens <= 0 or reasoning_rate is None:
        # No reasoning to re-price, or no separate rate to re-price it at --
        # either way the output rate already bills every output token.
        return _per_1k_cost(output_tokens, output_rate), "complete"
    # Clamp defensively: a malformed report claiming more reasoning than output
    # must not price a negative visible-output leg.
    visible_tokens = max(0, output_tokens - reasoning_tokens)
    total = _per_1k_cost(visible_tokens, output_rate) + _per_1k_cost(
        reasoning_tokens, reasoning_rate
    )
    return total, "complete"


def price_tokens(
    *,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    reasoning_tokens: int | None = None,
    prices_path: Path | None = None,
) -> PricedTokens:
    """Estimate USD from the frozen price table. Never returns a silent zero."""
    path = prices_path or PRICES_PATH
    prices_doc = load_prices(path)
    version = _price_table_version(prices_doc)

    if model is None or input_tokens is None or output_tokens is None:
        return PricedTokens(None, "unknown", version)

    try:
        price_key = require_price_key(model, prices=prices_doc)
    except KeyError:
        return PricedTokens(None, "unknown", version)

    row = prices_doc[price_key]
    output_total, status = _output_leg(
        output_tokens,
        reasoning_tokens,
        row["output_per_1k"],
        row.get("reasoning_per_1k"),
    )
    total = _per_1k_cost(input_tokens, row["input_per_1k"]) + output_total
    if total <= Decimal("0"):
        return PricedTokens(None, "unknown", version)
    return PricedTokens(total.quantize(_USD_QUANT), status, version)
