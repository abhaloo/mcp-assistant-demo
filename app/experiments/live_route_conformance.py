"""Live route conformance: acquire, build model, probe, commit per route."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from app.config import settings
from app.experiments.arming import require_armed
from app.experiments.core.secret_redaction import redact_secrets
from app.experiments.route_conformance import (
    FAILURE_MODEL_BUILD_FAILED,
    FAILURE_PROBE_RAISED,
    ConformanceReport,
    RouteProbe,
    RouteProbeResult,
    check_route_preconditions,
    evaluate_conformance,
    failed_result,
    probe_built_route,
    probe_route,
)
from app.experiments.spend_coordinator import (
    acquire,
    charge_for,
    commit,
    release,
)
from app.pricing.prices import require_price_key
from app.providers.factory import get_chat_model

PHASE = "route_conformance"
# A one-token round trip costs fractions of a cent on every route in the
# registry; 1c each is a deliberate over-reserve so a slow/verbose provider
# cannot overrun. The phase ceiling in PHASE_BUDGETS is the real bound.
DEFAULT_EST_USD = 0.01

# --- Campaign staging factors (see tasks/routing_task.py:freeze_inputs) -------
#
# DEFAULT_EST_USD is an over-reserve for the EXPECTED case, but it is a flat
# constant that knows nothing about the deployment's rate. Publishing it unchanged
# as both the per-cell forecast AND the per-cell ceiling gives Campaign zero
# headroom: `Campaign.enforce_cell_ceiling` kills the whole attempt on any row
# where `cost_usd > ceiling + 1e-9`, so a route that bills one cent more than the
# estimate stops a sweep whose central invariant is that one bad route does not.
# Same shape as SQL's `live_sql_eval.CELL_CEILING_FACTOR` / `CELL_FORECAST_FACTOR`,
# with routing's own numbers.
#
# Sized against the physical bound rather than copied. The most expensive priced
# deployment is gpt-4o at $0.015/1k output (prices.yaml:11-13) and the probe asks
# for at most REASONING_PROBE_MAX_TOKENS = 256 output tokens, so an HONOURED
# ceiling costs at most ~256 x $0.000015 ~= $0.004 -- comfortably under a $0.02
# forecast. The interesting case is an UNHONOURED one: this module exists because
# token-ceiling parameters get silently dropped or renamed on the wire, and a
# provider that ignores ours falls back to its own default max (commonly 4096
# tokens) -> ~$0.061. Ceiling $0.08 therefore RECORDS that blowout and lets the
# sweep finish, while a genuine runaway (10k+ tokens, or a mispriced model) still
# trips it.
CELL_FORECAST_FACTOR = 2.0
CELL_CEILING_FACTOR = 8.0
# The answer reservation is n x forecast; this pads it so ONE route billing above
# its forecast does not fail `work_matrix.forecast_fits` at the next batch
# boundary on a small matrix.
RESERVE_SAFETY_FACTOR = 1.25

# Double-armed like B0: a stray pytest run with a loaded .env cannot spend.
ROUTE_CONFORMANCE_LIVE_ENV = "ROUTE_CONFORMANCE_LIVE"


def require_live_armed() -> None:
    """The arming gate for BOTH entry points — this driver and ``RoutingTask``.

    Public because the campaign adapter needs it at construction time, before
    Campaign does anything at all; restating its body there is how the two
    gates drift apart.
    """
    require_armed(ROUTE_CONFORMANCE_LIVE_ENV, purpose="live route conformance")


def run_live_route_conformance(
    *,
    probes: Sequence[RouteProbe],
    est_usd_per_route: float = DEFAULT_EST_USD,
    ledger_path: Path | None = None,
    acquire_fn: Callable[..., str] = acquire,
    commit_fn: Callable[..., None] = commit,
    release_fn: Callable[..., None] = release,
    get_model_fn: Callable[..., Any] = get_chat_model,
    probe_fn: Callable[..., RouteProbeResult] = probe_route,
    price_check_fn: Callable[..., str] = require_price_key,
) -> ConformanceReport:
    """Probe every route under the spend ledger and return a fail-closed report."""
    require_live_armed()

    results: list[RouteProbeResult] = []
    for probe in probes:
        # Both pre-spend checks, shared with RoutingTask. They run BEFORE acquire:
        # reserving on a route we already know is unprobeable would spend the
        # phase budget to learn nothing.
        blocked = check_route_preconditions(
            probe, environment=settings.environment, price_check_fn=price_check_fn
        )
        if blocked is not None:
            results.append(blocked)
            continue

        # A reservation failure on one route must not discard every result
        # already gathered — the docstring promises the sweep continues, and a
        # partial picture is how you relaunch into a second dead route.
        try:
            reservation_id = acquire_fn(
                PHASE,
                probe.deployment,
                est_usd_per_route,
                ledger_path=ledger_path,
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                failed_result(probe, "reservation_failed", f"{type(exc).__name__}: {exc}")
            )
            continue

        result = probe_built_route(probe, get_model_fn=get_model_fn, probe_fn=probe_fn)

        # Settle the ledger from the SHARED failure vocabulary rather than from a
        # second copy of the branch structure: nothing was sent for a failed build
        # (release), the probe may have spent before it threw (commit the
        # reservation), anything else is priced from real usage.
        if FAILURE_MODEL_BUILD_FAILED in result.failures:
            try:
                release_fn(reservation_id, ledger_path=ledger_path)
            except Exception as release_exc:  # noqa: BLE001 — keep the sweep alive
                results.append(
                    failed_result(
                        probe,
                        "reservation_leaked",
                        f"{result.error} (release also failed: "
                        f"{type(release_exc).__name__}: {release_exc})",
                    )
                )
                continue
            results.append(result)
            continue

        evidence = result.evidence or {}
        charge = (
            est_usd_per_route
            if FAILURE_PROBE_RAISED in result.failures
            else charge_for(
                probe.deployment,
                input_tokens=int(evidence.get("input_tokens") or 0),
                output_tokens=int(evidence.get("output_tokens") or 0),
                reserved_usd=est_usd_per_route,
            )
        )
        # spend_coordinator.commit RECORDS the reservation_overrun / cap violation
        # and THEN raises. Unguarded, that raise escapes this loop and throws away
        # every result already gathered — precisely the partial picture this
        # module's docstring forbids. The violation is already durable in the
        # ledger (and blocks the next acquire, which surfaces as reservation_failed
        # on the following routes), so the right move here is to record it against
        # THIS route and keep sweeping.
        try:
            commit_fn(reservation_id, charge, ledger_path=ledger_path)
        except Exception as commit_exc:  # noqa: BLE001 — keep the sweep alive
            # Fail CLOSED — spend we could not book is not attestable conformance —
            # but keep the route's own wire evidence, so the operator can see the
            # ROUTE was healthy and only the ACCOUNTING failed.
            results.append(
                RouteProbeResult(
                    probe=probe,
                    passed=False,
                    failures=(*result.failures, "spend_commit_failed"),
                    evidence=result.evidence,
                    error=redact_secrets(
                        f"{result.error} (commit failed: {type(commit_exc).__name__}: {commit_exc})"
                        if result.error
                        else f"commit failed: {type(commit_exc).__name__}: {commit_exc}"
                    ),
                )
            )
            continue
        results.append(result)

    return evaluate_conformance(results)
