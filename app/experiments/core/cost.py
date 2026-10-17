"""Shared cost derivation and cost_source labels for experiment tasks."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.experiments.core.money import estimate_cost_usd

# cost_source vocabulary — "usage" is measured; "ceiling*" is fail-closed.
CEILING_PREFIX = "ceiling"

SOURCE_USAGE = "usage"

#: Nothing can be priced without knowing what actually ran.
CEILING_NO_DEPLOYMENT = "ceiling_no_deployment"
#: The provider reported no usable token usage at all.
CEILING_NO_USAGE = "ceiling_fail_closed"
#: The row claims LLM calls but reported zero tokens on one side -- a PARTIAL capture.
CEILING_PARTIAL_USAGE = "ceiling_partial_usage"
#: The price table could not answer for this deployment.
CEILING_UNPRICED = "ceiling_unpriced"
#: SQL: ``run_case`` itself raised. The call may have spent before it threw.
CEILING_RUNNER_ERROR = "ceiling_runner_error"
CEILING_PROBE_RAISED = "ceiling_probe_raised"
# Pre-provider failure — legitimate zero charge.
SOURCE_UNSPENT = "unspent_no_provider_call"

PRICING_ERRORS = (KeyError, OSError, ValueError, TypeError)

__all__ = [
    "CEILING_NO_DEPLOYMENT",
    "CEILING_NO_USAGE",
    "CEILING_PARTIAL_USAGE",
    "CEILING_PREFIX",
    "CEILING_PROBE_RAISED",
    "CEILING_RUNNER_ERROR",
    "CEILING_UNPRICED",
    "PRICING_ERRORS",
    "SOURCE_UNSPENT",
    "SOURCE_USAGE",
    "PricedCost",
    "price_or_ceiling",
]


@dataclass(frozen=True)
class PricedCost:
    """One row's cost evidence: the charge, why it is that number, and what it priced.

    Frozen: the rule decides once. ``deployment`` is the deployment the price was
    actually taken against (``None`` when there was none), which is what belongs on
    the row -- for a router arm the requested arm label has no single price.
    """

    usd: float
    source: str
    deployment: str | None


def price_or_ceiling(
    *,
    deployment: str | None,
    tokens_in: int,
    tokens_out: int,
    ceiling_usd: float,
    n_llm_calls: int = 0,
    cost_fn: Callable[[str, int, int], float] | None = None,
) -> PricedCost:
    """Price one row from its token usage, failing CLOSED at ``ceiling_usd``.

    ``ceiling_usd`` is the caller's fail-closed charge, not necessarily a hard upper
    bound: SQL passes the frozen ``answer_cell_ceiling_usd`` for the arm, routing
    passes its flat per-route estimate (the amount its standalone CLI COMMITS when a
    probe may have spent). Both are "the safest number we can defend charging when
    the real one is unknowable".

    The prompt-cache discount is deliberately NOT applied -- prices.yaml carries no
    cached rate -- so the charge over-states rather than under-states real spend.

    :param n_llm_calls: how many provider calls the row claims. Default 0 means "the
        caller does not track this", which disables the partial-capture branch rather
        than asserting the row is consistent.
    :param cost_fn: the pricer. ``None`` resolves ``estimate_cost_usd`` HERE, at call
        time, rather than snapshotting it into a default argument at import time --
        so patching this module's pricer really does change every adapter's charge,
        which is the property that makes "one rule" checkable rather than asserted.
    """
    pricer = cost_fn if cost_fn is not None else estimate_cost_usd
    if not math.isfinite(ceiling_usd) or ceiling_usd < 0:
        # The ONE place raising is the fail-closed answer. Every branch below falls
        # back to this number; if it is itself corrupt, returning it would launder the
        # corruption into the ledger AS A CHARGE. Checked before any branch that would
        # use it, so a broken frozen contract is caught on the first row rather than
        # on the first row that happens to fail.
        raise ValueError(
            f"fail-closed ceiling must be finite and non-negative, got {ceiling_usd!r}"
        )

    if not deployment:
        return PricedCost(usd=ceiling_usd, source=CEILING_NO_DEPLOYMENT, deployment=None)
    if tokens_in + tokens_out <= 0:
        return PricedCost(usd=ceiling_usd, source=CEILING_NO_USAGE, deployment=deployment)
    if n_llm_calls > 0 and (tokens_in <= 0 or tokens_out <= 0):
        # Internally inconsistent, so the usage capture is PARTIAL: CapturingCallback
        # accumulates per LLM call and contributes 0 for any response that reports no
        # usage at all, but a completed call always has a non-empty prompt AND emits at
        # least one completion token. Pricing a partial capture would under-charge the
        # ledger and, because arms can use different providers, would bias a gate's
        # candidate-vs-baseline cost ratio toward whichever reports usage worse.
        return PricedCost(usd=ceiling_usd, source=CEILING_PARTIAL_USAGE, deployment=deployment)

    try:
        cost = float(pricer(deployment, tokens_in, tokens_out))
    except PRICING_ERRORS:
        return PricedCost(usd=ceiling_usd, source=CEILING_UNPRICED, deployment=deployment)
    if not math.isfinite(cost) or cost < 0:
        # `NaN > ceiling` is False, so a bare comparison downstream waves NaN through
        # (the same trap work_matrix.forecast_fits documents). A NEGATIVE cost is worse:
        # it does not merely evade the ceiling, it SUBTRACTS from accounted spend and
        # buys headroom under the campaign cap that was never earned.
        return PricedCost(usd=ceiling_usd, source=CEILING_NO_USAGE, deployment=deployment)
    return PricedCost(usd=cost, source=SOURCE_USAGE, deployment=deployment)
