"""The campaign spend shape, chosen once at attempt start.

A ``SpendPlan`` replaces the ``spend_stages`` truthiness test and the
``preflight_oracle is not None`` test that decide how a reservation opens,
runs, and settles -- the nine sites ``run_attempt`` would otherwise re-derive
that decision at. :func:`spend_plan_for` selects the shape once, and every
later spend-shape decision is a method call on the chosen plan instead of a
re-test of those booleans.

One ``preflight_oracle is not None`` test stays in ``run_attempt`` outside
this seam: it guards ``oracle.reset_for_attempt()``, which depends on
whether the ORACLE's protocol supports reset, not on the spend shape, so it
is a different question from the one this module answers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from app.experiments.core.answer_matrix import AnswerMatrix
from app.experiments.core.errors import RagExperimentError
from app.experiments.core.money import (
    BUDGET_EPSILON_USD,
    require_finite_nonnegative_usd,
    require_finite_positive_usd,
    round_reservation_usd,
)
from app.experiments.core.protocol import Oracle, PreflightOracle
from app.experiments.core.spend_staging import SpendStaging

__all__ = ["SpendPlan", "StagedFreePlan", "StagedPaidPlan", "UnstagedPlan", "spend_plan_for"]


class SpendPlan(Protocol):
    """One campaign spend shape: unstaged, staged-free, or staged-paid.

    Each method owns one decision that a direct boolean test on
    ``spend_stages`` or ``preflight_oracle`` would otherwise make. No
    implementation may re-read either of those to decide what to do -- the
    shape was already decided when the plan was selected.
    """

    def open_answer_stage(
        self,
        staging: SpendStaging,
        *,
        oracle: PreflightOracle | None,
        frozen_inputs: Mapping[str, Any],
        runtime_invariant_fn: Callable[[str], None],
    ) -> None:
        """Open whatever reservation(s) the answer stage needs before it runs."""
        ...

    def run_answers(
        self,
        matrix: AnswerMatrix,
        *,
        frozen_inputs: Mapping[str, Any],
        on_row: Callable[[dict[str, Any]], None],
    ) -> None:
        """Drive the answer matrix, staged or unstaged."""
        ...

    def settle_answers(self, staging: SpendStaging) -> None:
        """Settle the answer reservation, if this shape opened one."""
        ...

    def open_oracle_stage(self, staging: SpendStaging, *, oracle: PreflightOracle | None) -> None:
        """Open whatever reservation the judge stage needs, if any."""
        ...

    def settle_oracle(self, staging: SpendStaging, cost_usd: float) -> None:
        """Settle the judge reservation at its actual cost, if one is open."""
        ...

    def settle_final(self, staging: SpendStaging) -> None:
        """Settle the whole-campaign reservation, if this shape opened one."""
        ...


class UnstagedPlan:
    """No ``spend_stages`` on the freeze: one reservation covers the whole attempt."""

    def open_answer_stage(
        self,
        staging: SpendStaging,
        *,
        oracle: PreflightOracle | None,
        frozen_inputs: Mapping[str, Any],
        runtime_invariant_fn: Callable[[str], None],
    ) -> None:
        staging.enter_stage("campaign", reserve_usd=staging.campaign_cap_usd)

    def run_answers(
        self,
        matrix: AnswerMatrix,
        *,
        frozen_inputs: Mapping[str, Any],
        on_row: Callable[[dict[str, Any]], None],
    ) -> None:
        matrix.run_unstaged(on_row=on_row)

    def settle_answers(self, staging: SpendStaging) -> None:
        """No answer-stage reservation exists to settle separately."""

    def open_oracle_stage(self, staging: SpendStaging, *, oracle: PreflightOracle | None) -> None:
        """No oracle-stage reservation exists to open separately."""

    def settle_oracle(self, staging: SpendStaging, cost_usd: float) -> None:
        """No oracle-stage reservation exists to settle separately."""

    def settle_final(self, staging: SpendStaging) -> None:
        staging.settle(staging.actual_spend_usd)


class _StagedPlan:
    """Shared behaviour for the two staged shapes.

    Both hold the frozen ``spend_stages`` mapping and read *values* from it --
    neither asks whether it is truthy, because ``spend_plan_for`` already made
    that call. Only how the answer and oracle stages OPEN differs between a
    plain :class:`Oracle` (:class:`StagedFreePlan`) and a
    :class:`PreflightOracle` (:class:`StagedPaidPlan`); settling and running
    the matrix are identical once the reservation exists.
    """

    def __init__(self, spend_stages: Mapping[str, Any]) -> None:
        self._spend_stages = spend_stages

    def run_answers(
        self,
        matrix: AnswerMatrix,
        *,
        frozen_inputs: Mapping[str, Any],
        on_row: Callable[[dict[str, Any]], None],
    ) -> None:
        matrix.run_probe_cells()
        matrix.order(frozen_inputs.get("answer_order_seed"))
        matrix.check_forecast()
        matrix.run_staged(on_row=on_row)

    def settle_answers(self, staging: SpendStaging) -> None:
        staging.settle(staging.answer_spend_usd)

    def settle_oracle(self, staging: SpendStaging, cost_usd: float) -> None:
        if staging.has_active_reservation:
            staging.settle(cost_usd)

    def settle_final(self, staging: SpendStaging) -> None:
        """A staged campaign settles the answer and oracle stages as they close, not here."""


class StagedFreePlan(_StagedPlan):
    """``spend_stages`` on the freeze, but the oracle is a plain, free :class:`Oracle`.

    Skips the canary and the held-oracle reservation entirely -- there is
    nothing to preflight and nothing to hold a reservation open for.
    """

    def open_answer_stage(
        self,
        staging: SpendStaging,
        *,
        oracle: PreflightOracle | None,
        frozen_inputs: Mapping[str, Any],
        runtime_invariant_fn: Callable[[str], None],
    ) -> None:
        runtime_invariant_fn("before_answers")
        staging.enter_stage(
            "answers",
            reserve_usd=round_reservation_usd(float(self._spend_stages["answer_reserve_usd"])),
        )

    def open_oracle_stage(self, staging: SpendStaging, *, oracle: PreflightOracle | None) -> None:
        staging.enter_oracle_stage_unreserved()


class StagedPaidPlan(_StagedPlan):
    """``spend_stages`` on the freeze and a :class:`PreflightOracle` to canary first."""

    def open_answer_stage(
        self,
        staging: SpendStaging,
        *,
        oracle: PreflightOracle | None,
        frozen_inputs: Mapping[str, Any],
        runtime_invariant_fn: Callable[[str], None],
    ) -> None:
        assert oracle is not None  # spend_plan_for refused this shape without one
        staging.enter_stage(
            "judge_canary", reserve_usd=float(self._spend_stages["canary_reserve_usd"])
        )
        staging.oracle_started()
        preflight_validation = oracle.preflight(frozen_inputs)
        preflight_cost = float(preflight_validation.details.get("cost_usd") or 0.0)
        preflight_cost = require_finite_nonnegative_usd(
            preflight_cost, what="judge canary returned invalid cost evidence"
        )
        staging.note_spend(preflight_cost)
        staging.settle(preflight_cost)
        oracle.mark_preflight_cost_accounted()
        staging.oracle_finished()
        if not (preflight_validation.canaries_passed and preflight_validation.call_evidence_passed):
            raise RagExperimentError("judge canary preflight failed")
        runtime_invariant_fn("before_answers")
        forecast = float(preflight_validation.details.get("forecast_main_cost_usd") or 0.0)
        forecast = require_finite_positive_usd(
            forecast, what="judge canary returned invalid main-cost forecast"
        )
        held_oracle_reserve_usd = round_reservation_usd(forecast)
        if (
            held_oracle_reserve_usd
            > float(self._spend_stages["judge_reserve_usd"]) + BUDGET_EPSILON_USD
        ):
            raise RagExperimentError("judge cost forecast exceeds the frozen judge limit")
        # Cap check before entering answer stage reservations.
        answer_reserve_usd = round_reservation_usd(float(self._spend_stages["answer_reserve_usd"]))
        if (
            staging.actual_spend_usd + held_oracle_reserve_usd + answer_reserve_usd
            > staging.campaign_cap_usd + BUDGET_EPSILON_USD
        ):
            raise RagExperimentError(
                "forecast judge and answer reservations exceed the campaign cap"
            )
        # Held before the answers reserve: a campaign that cannot afford the oracle must
        # discover it before it spends the matrix, and a failed answer reservation still
        # leaves the held one on the books for unwind to release.
        staging.hold_oracle(held_oracle_reserve_usd)
        staging.enter_stage("answers", reserve_usd=answer_reserve_usd)

    def open_oracle_stage(self, staging: SpendStaging, *, oracle: PreflightOracle | None) -> None:
        assert oracle is not None  # spend_plan_for refused this shape without one
        available_usd = staging.enter_oracle_stage_on_hold()
        oracle.configure_budget(available_usd=available_usd)


def spend_plan_for(spend_stages: Mapping[str, Any] | None, oracle: Oracle) -> SpendPlan:
    """Choose the campaign's spend shape once, before an attempt directory exists.

    A PAID oracle that cannot preflight is refused here, before anything can
    reserve: it could spend unboundedly with nothing having checked it first,
    which is exactly what the canary stage exists to prevent. Refusing at
    selection is what makes the refusal cost nothing
    (``test_paid_oracle_still_requires_preflight``).
    """
    preflight_oracle = oracle if isinstance(oracle, PreflightOracle) else None
    if spend_stages and bool(oracle.is_paid) and preflight_oracle is None:
        raise RagExperimentError("staged spend requires a judge canary preflight seam")
    if not spend_stages:
        return UnstagedPlan()
    if preflight_oracle is not None:
        return StagedPaidPlan(spend_stages)
    return StagedFreePlan(spend_stages)
