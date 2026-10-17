"""Routing TaskAdapter: route-conformance probes through Campaign."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.experiments import live_route_conformance
from app.experiments.core.checkpoint import cell_set_hash
from app.experiments.core.cost import (
    CEILING_PROBE_RAISED,
    SOURCE_UNSPENT,
    price_or_ceiling,
)
from app.experiments.core.errors import RagExperimentError
from app.experiments.core.money import (
    BUDGET_EPSILON_USD,
    estimate_cost_usd,
    round_reservation_usd,
)
from app.experiments.core.protocol import Oracle, OracleValidation
from app.experiments.core.work_matrix import Cell
from app.experiments.live_route_conformance import (
    CELL_CEILING_FACTOR,
    CELL_FORECAST_FACTOR,
    DEFAULT_EST_USD,
    RESERVE_SAFETY_FACTOR,
)
from app.experiments.route_conformance import (
    FAILURE_PROBE_RAISED,
    STATUS_BROKEN,
    STATUS_CONFORMANT,
    UNSPENT_FAILURES,
    RouteProbe,
    RouteProbeResult,
    evaluate_conformance,
    probe_one_route,
    probe_route,
)
from app.pricing.prices import require_price_key
from app.providers.factory import get_chat_model
from app.providers.model_purpose import ModelPurpose

__all__ = ["RoutingOracle", "RoutingTask", "RoutingVerdict", "routing_gate"]

# Routing cells have no arm axis; route_id distinguishes each probe.
ARM_NAME = "route_probe"


def routing_gate(status: str) -> str:
    """Assert status is ROUTES-CONFORMANT or ROUTE-BROKEN."""
    if status not in (STATUS_CONFORMANT, STATUS_BROKEN):
        raise RagExperimentError(f"unrecognized route conformance status: {status!r}")
    return status


@dataclass(frozen=True)
class RoutingVerdict:
    """``RoutingTask.finalize()``'s return type.

    Campaign only reads ``.status`` off this (for the progress callback) --
    see ``app/experiments/core/protocol.py``'s ``TaskAdapter.finalize`` docstring.
    """

    status: str
    broken_routes: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def _probe_document(probe: RouteProbe) -> dict[str, Any]:
    return {
        "purpose": str(probe.purpose),
        "deployment": probe.deployment,
        "credential_source": probe.credential_source,
        "client_kind": probe.client_kind,
        "environment": probe.environment,
        "returns_reasoning": probe.returns_reasoning,
    }


def _hash_probes(probes: Sequence[RouteProbe]) -> str:
    payload = json.dumps(
        [_probe_document(probe) for probe in probes],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _row_to_probe(row: dict[str, Any]) -> RouteProbe:
    return RouteProbe(
        purpose=ModelPurpose(row["purpose"]),
        deployment=row["deployment"],
        credential_source=row["credential_source"],
        client_kind=row["client_kind"],
        environment=row["environment"],
        returns_reasoning=bool(row.get("returns_reasoning", False)),
    )


def _row_to_result(row: dict[str, Any]) -> RouteProbeResult:
    return RouteProbeResult(
        probe=_row_to_probe(row),
        passed=bool(row["passed"]),
        failures=tuple(row.get("failures") or ()),
        evidence=dict(row.get("evidence") or {}),
        error=row.get("error"),
    )


class RoutingOracle:
    """Free oracle wrapping evaluate_conformance over probe rows."""

    is_paid = False

    def score(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], OracleValidation]:
        results = [_row_to_result(row) for row in rows]
        report = evaluate_conformance(results)
        status = routing_gate(report.status)
        validation = OracleValidation(
            canaries_passed=True,
            repeatability_passed=True,
            call_evidence_passed=status == STATUS_CONFORMANT,
            details={
                "cost_usd": 0.0,
                "status": status,
                "broken_routes": list(report.broken_routes),
            },
        )
        return rows, validation


class RoutingTask:
    """Routing TaskAdapter over a frozen route matrix."""

    def __init__(
        self,
        *,
        probes: Sequence[RouteProbe],
        campaign_cap_usd: float,
        environment: str,
        est_usd_per_route: float = DEFAULT_EST_USD,
        get_model_fn: Callable[..., Any] = get_chat_model,
        probe_fn: Callable[[Any, RouteProbe], RouteProbeResult] = probe_route,
        price_check_fn: Callable[[str], str] = require_price_key,
        cost_fn: Callable[[str, int, int], float] = estimate_cost_usd,
        oracle: Oracle | None = None,
        require_live_arming: bool = True,
    ) -> None:
        if require_live_arming:
            # The CLI driver's own gate, CALLED -- not restated. Two copies of an
            # arming check is two places for one of them to stop refusing.
            live_route_conformance.require_live_armed()
        self._probes: tuple[RouteProbe, ...] = tuple(probes)
        route_ids = [probe.route_id for probe in self._probes]
        duplicates = sorted({route_id for route_id in route_ids if route_ids.count(route_id) > 1})
        if duplicates:
            raise RagExperimentError(f"duplicate routes in matrix: {duplicates}")
        self._probe_by_route_id = {probe.route_id: probe for probe in self._probes}
        self._campaign_cap_usd = float(campaign_cap_usd)
        self._environment = environment
        self._est_usd_per_route = float(est_usd_per_route)
        self._get_model_fn = get_model_fn
        self._probe_fn = probe_fn
        self._price_check_fn = price_check_fn
        self._cost_fn = cost_fn
        self._oracle = oracle or RoutingOracle()
        self._freeze_hash = _hash_probes(self._probes)

    def oracle(self) -> Oracle:
        return self._oracle

    def freeze_inputs(self) -> dict[str, Any]:
        inputs: dict[str, Any] = {
            "freeze_hash": self._freeze_hash,
            "runtime": {"environment": self._environment, "route_count": len(self._probes)},
            "campaign_cap_usd": self._campaign_cap_usd,
        }
        # An empty matrix has nothing to reserve. round_reservation_usd()
        # raises on a non-positive amount (by design), and Campaign's
        # needs_preflight=False branch re-normalizes answer_reserve_usd
        # through it unconditionally -- so staging a $0 answer reservation
        # would blow up inside Campaign with a ValueError instead of letting
        # the (empty) matrix reach the oracle for a proper ROUTE-BROKEN
        # verdict. Omitting spend_stages here falls back to Campaign's
        # non-staged "campaign cap" branch, which has no such constraint.
        if self._probes:
            # THREE DIFFERENT NUMBERS, deliberately -- see the factor rationale in
            # live_route_conformance.py. Publishing the flat estimate as all three
            # gave the ceiling zero headroom, and Campaign.enforce_cell_ceiling
            # aborts the WHOLE attempt on `cost_usd > ceiling + 1e-9`. That turned
            # "one route cost a cent more than we guessed" into a dead sweep --
            # breaking the invariant this adapter exists to uphold for broken
            # routes ("one broken route must not stop the sweep") for expensive
            # ones instead.
            forecast_usd = round_reservation_usd(self._est_usd_per_route * CELL_FORECAST_FACTOR)
            ceiling_usd = round_reservation_usd(self._est_usd_per_route * CELL_CEILING_FACTOR)
            answer_reserve_usd = round_reservation_usd(
                len(self._probes) * forecast_usd * RESERVE_SAFETY_FACTOR
            )
            inputs["spend_stages"] = {
                "answer_reserve_usd": answer_reserve_usd,
                "answer_cell_ceiling_usd": {ARM_NAME: ceiling_usd},
                "answer_cell_forecast_usd": {ARM_NAME: forecast_usd},
            }
        return inputs

    def cells(self) -> list[Cell]:
        return [Cell(case_id=probe.route_id, arm_name=ARM_NAME, repeat=0) for probe in self._probes]

    def probe_cells(self) -> list[Cell]:
        """Empty: every cell here is already one cheap, independent route
        probe -- there is no RAG-style per-arm pilot to gate the rest of the
        matrix behind."""
        return []

    def execute(self, cell: Cell) -> dict[str, Any]:
        """Run one route through the SHARED per-route driver and shape a row.

        The four failure branches (environment mismatch, unpriceable, model build
        failed, probe raised) and every ``redact_secrets`` call now live once, in
        ``route_conformance.probe_one_route``. This method's only remaining job is
        the part that genuinely differs from the CLI: turning a ``RouteProbeResult``
        into a Campaign row, and pricing it for Campaign to stage.
        """
        probe = self._probe_by_route_id[cell.case_id]
        result = probe_one_route(
            probe,
            environment=self._environment,
            get_model_fn=self._get_model_fn,
            probe_fn=self._probe_fn,
            price_check_fn=self._price_check_fn,
        )
        cost_usd, cost_source = self._charge_for(probe, result)
        return _probe_document(probe) | {
            "case_id": cell.case_id,
            "arm": cell.arm_name,
            "repeat": cell.repeat,
            "cost_usd": cost_usd,
            "cost_source": cost_source,
            "passed": result.passed,
            "failures": list(result.failures),
            "evidence": dict(result.evidence),
            "error": result.error,
        }

    def _charge_for(self, probe: RouteProbe, result: RouteProbeResult) -> tuple[float, str]:
        """What Campaign should stage for this row -- the CLI's ledger rules, as a cost.

        Same three-way split the CLI applies to its reservation, driven by the same
        ``route_conformance`` failure constants: nothing reached the provider (the
        CLI releases) -> $0; the probe may have spent before it threw (the CLI
        commits the reservation) -> the estimate; otherwise price the real usage.

        The first two are LEDGER decisions, not prices, so they stay here -- there is
        no usage to price in either. The third goes through ``core/cost``'s
        ``price_or_ceiling``, the one rule ``SqlTask`` also uses, which buys routing
        three fail-closed branches ``actual_usd`` never had: a pricer that raises
        anything other than ``KeyError`` (an unreadable prices.yaml would otherwise
        propagate out of here and kill the whole sweep -- the main risk this adapter
        prevents), a non-finite price, and a NEGATIVE one, which would not merely evade
        Campaign's per-cell ceiling but SUBTRACT from accounted spend.

        ``est_usd_per_route`` is passed as the fail-closed charge, NOT the frozen cell
        ceiling (``est x CELL_CEILING_FACTOR``): the estimate is what the standalone
        CLI commits for a call that may have spent, and charging 8x that on every
        usage-missing route would inflate the campaign's accounted spend against its
        own cap. Note the honest limit -- unlike SQL's ceiling, the estimate is not an
        upper bound, so a route that really cost more than $0.01 and reported no usage
        is still UNDER-charged. That is ``actual_usd``'s pre-existing behaviour, kept
        deliberately so the two entry points still agree (see the equivalence test in
        tests/experiments/tasks/test_routing_task.py).

        ``n_llm_calls`` is left at its default 0: one probe is one call, and routing
        captures no call COUNT to contradict a one-sided usage report with. Passing 1
        would make a probe reporting input-but-no-output tokens charge the estimate,
        which is defensible but is a behaviour change, and it would break the
        equivalence this slice deliberately preserves.
        """
        if UNSPENT_FAILURES.intersection(result.failures):
            return 0.0, SOURCE_UNSPENT
        if FAILURE_PROBE_RAISED in result.failures:
            return self._est_usd_per_route, CEILING_PROBE_RAISED
        evidence = result.evidence or {}
        priced = price_or_ceiling(
            deployment=probe.deployment,
            tokens_in=int(evidence.get("input_tokens") or 0),
            tokens_out=int(evidence.get("output_tokens") or 0),
            ceiling_usd=self._est_usd_per_route,
            cost_fn=self._cost_fn,
        )
        return priced.usd, priced.source

    @staticmethod
    def classify_failure(row: dict[str, Any]) -> str | None:
        if row.get("passed"):
            return None
        failures = row.get("failures") or ()
        return ",".join(failures) if failures else "route_probe_failed"

    @staticmethod
    def evidence_failures(row: dict[str, Any], *, stage: str = "final") -> list[str]:
        """Always empty -- see the module docstring's second deviation note.
        A broken route is scored (classify_failure above, routing_gate in
        finalize below), never treated as a provenance bug in the harness
        itself -- Campaign's validate_row would otherwise abort the whole
        sweep on the first broken route.

        ``stage`` is accepted and ignored, because the protocol declares it."""
        del row, stage
        return []

    def enforce_operational_budget(self, rows: list[dict[str, Any]]) -> None:
        """No-op by design (see module docstring): routing has no RAG-style
        runaway-spend risk to guard against (every cell costs about the same,
        near-zero amount), and a route "failing" IS the sweep's job, not a
        signal to stop early."""
        del rows

    def verify_frozen_contract(self) -> None:
        if _hash_probes(self._probes) != self._freeze_hash:
            raise RagExperimentError("route matrix drifted from its frozen snapshot")

    def checkpoint_identity(self) -> dict[str, Any]:
        cells = self.cells()
        cell_keys = [(cell.case_id, cell.arm_name, cell.repeat) for cell in cells]
        return {
            "schema_version": 1,
            "task_kind": "routing",
            "freeze_hash": self._freeze_hash,
            "expected_row_count": len(cells),
            "expected_cell_set_hash": cell_set_hash(cell_keys),
            "judge_policy_hash": f"sha256:{hashlib.sha256(self._freeze_hash.encode()).hexdigest()}",
        }

    def cell_provider(self, cell: Cell) -> str | None:
        del cell
        return None

    def finalize(
        self,
        *,
        scored_rows: list[dict[str, Any]],
        oracle_validation: OracleValidation,
        actual_spend_usd: float,
        store: Any,
    ) -> RoutingVerdict:
        status = routing_gate(str(oracle_validation.details.get("status") or STATUS_BROKEN))
        broken_routes = tuple(oracle_validation.details.get("broken_routes") or ())
        details: dict[str, Any] = {"broken_routes": list(broken_routes)}
        if actual_spend_usd > self._campaign_cap_usd + BUDGET_EPSILON_USD:
            details = {
                "reason": "campaign_cap_exceeded",
                "campaign_cap_usd": self._campaign_cap_usd,
                "accounted_spend_usd": actual_spend_usd,
                "underlying_status": status,
                "broken_routes": list(broken_routes),
            }
            status = STATUS_BROKEN
        verdict = RoutingVerdict(
            status=routing_gate(status), broken_routes=broken_routes, details=details
        )
        store.seal_verdict(
            scored_rows=scored_rows,
            oracle_validation=oracle_validation,
            verdict=verdict,
            oracle_validation_filename="oracle_validation.json",
        )
        return verdict
