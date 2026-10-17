"""Spend reservation staging and abort-time unwind."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.experiments.core.errors import RagExperimentError


class SpendStaging:
    """One attempt's staging state: which reservation is live, which stage it
    belongs to, what has been spent, and -- on abort -- what to do about it."""

    def __init__(
        self,
        *,
        reserve_fn: Callable[[float], str],
        commit_fn: Callable[[str, float], None],
        release_fn: Callable[[str], None],
    ) -> None:
        self._reserve_fn = reserve_fn
        self._commit_fn = commit_fn
        self._release_fn = release_fn
        self._reset(staged=False, campaign_cap_usd=0.0, oracle_is_paid=False)

    def _reset(self, *, staged: bool, campaign_cap_usd: float, oracle_is_paid: bool) -> None:
        self._staged = staged
        self._campaign_cap_usd = campaign_cap_usd
        self._oracle_is_paid = oracle_is_paid
        self._reservation_id: str | None = None
        self._held_judge_reservation_id: str | None = None
        self._held_judge_reserve_usd = 0.0
        self._active_stage = ""
        self._active_reserve_usd = 0.0
        self._oracle_started = False
        self._known_judge_spend_usd: float | None = None
        self._answer_spend_usd = 0.0
        self._actual_spend_usd = 0.0
        self._answer_row_count = 0

    def begin_attempt(self, *, staged: bool, campaign_cap_usd: float, oracle_is_paid: bool) -> None:
        """Start one attempt. Clears every piece of the previous attempt's state.

        A ``Campaign`` may run more than one attempt, and staging state that
        survived would let a previous run's held reservation or spend total
        decide this run's unwind.
        """
        self._reset(
            staged=bool(staged),
            campaign_cap_usd=float(campaign_cap_usd),
            oracle_is_paid=bool(oracle_is_paid),
        )

    # ------------------------------------------------------------- read-back

    @property
    def campaign_cap_usd(self) -> float:
        """The frozen cap, read once at ``begin_attempt``. Callers read it back from
        here rather than re-reading the frozen document, so the cap the guards check
        against and the cap ``unwind`` charges against cannot drift apart."""
        return self._campaign_cap_usd

    @property
    def actual_spend_usd(self) -> float:
        """Everything accounted so far: canary + answer cells + oracle."""
        return self._actual_spend_usd

    @property
    def answer_spend_usd(self) -> float:
        """The answer cells alone -- what the answer-stage reservation is settled at,
        and what the forecast guards measure against."""
        return self._answer_spend_usd

    @property
    def active_reserve_usd(self) -> float:
        """What the active stage reserved. The forecast guards measure the matrix
        against THIS rather than re-deriving it from the frozen document: the number
        the guard checks and the number ``unwind`` may have to foot are the same
        number, so they cannot drift."""
        return self._active_reserve_usd

    @property
    def has_active_reservation(self) -> bool:
        return self._reservation_id is not None

    def checkpoint_spend_evidence(self) -> dict[str, Any]:
        """Spend snapshot referenced by judge checkpoint receipts."""
        return {
            "actual_spend_usd": self.actual_spend_usd,
            "has_active_reservation": self.has_active_reservation,
        }

    # -------------------------------------------------------- stage machine

    def enter_stage(self, name: str, *, reserve_usd: float) -> None:
        """Make ``name`` the active stage and take its reservation.

        The stage and its reserve are set BEFORE the ledger call, exactly as the
        pre-J1 caller did: if ``reserve_fn`` raises there is no reservation to
        resolve, so ``unwind`` returns at its first check either way.
        """
        self._active_stage = name
        self._active_reserve_usd = reserve_usd
        self._reservation_id = self._reserve_fn(reserve_usd)

    def hold_oracle(self, reserve_usd: float) -> None:
        """Reserve the main oracle's budget now, to be promoted after the answers.

        Taken BEFORE the answer-stage reservation, and never before the canary has
        forecast it: the whole point is that the campaign discovers it cannot afford
        the oracle before it spends the matrix, not after.
        """
        self._held_judge_reserve_usd = reserve_usd
        self._held_judge_reservation_id = self._reserve_fn(reserve_usd)

    def enter_oracle_stage_on_hold(self) -> float:
        """Promote the held oracle reservation to active. Returns its size.

        No new reservation is taken -- the oracle's money was held before the
        answers ran. Refusing when nothing is held is the last thing standing
        between a paid oracle and spending against no reservation at all.
        """
        self._active_stage = "judge"
        if self._held_judge_reservation_id is None:
            raise RagExperimentError("main-judge reservation was not held before answers")
        self._active_reserve_usd = self._held_judge_reserve_usd
        self._reservation_id = self._held_judge_reservation_id
        self._held_judge_reservation_id = None
        return self._active_reserve_usd

    def enter_oracle_stage_unreserved(self) -> None:
        """The oracle stage for a free oracle: no reservation, nothing to promote."""
        self._active_stage = "judge"
        self._active_reserve_usd = 0.0
        self._reservation_id = None

    def settle(self, actual_usd: float) -> None:
        """Commit the active reservation at ``actual_usd`` and clear the live slot.

        Clearing BEFORE the ledger call is the invariant the four hand-written
        copies encoded: if ``commit_fn`` raises, or anything after it does, the
        abort handler must not find this reservation still live and commit it a
        second time.
        """
        reservation_id = self._reservation_id
        if reservation_id is None:
            raise RagExperimentError("settle() called with no active reservation")
        self._reservation_id = None
        self._commit_fn(reservation_id, actual_usd)

    # --------------------------------------------------------------- spend

    def note_spend(self, usd: float) -> None:
        """Accounted spend that is not an answer cell -- today, the canary."""
        self._actual_spend_usd += usd

    def note_cell_spend(self, usd: float) -> None:
        """One completed answer cell. Counted as a ROW as well as a cost: a free row
        that completed is still work the ledger must book, so the non-staged abort
        must commit at zero rather than release."""
        self._answer_spend_usd += usd
        self._actual_spend_usd += usd
        self._answer_row_count += 1

    def resettle_answers(self, *, total_usd: float, row_count: int) -> None:
        """Replace the accumulated answer spend with the settled figure.

        Deferred settlement revises COST as well as provenance -- the
        provider-reported number replaces the estimate — re-derived before commit.
        """
        self._actual_spend_usd = self._actual_spend_usd - self._answer_spend_usd + total_usd
        self._answer_spend_usd = total_usd
        self._answer_row_count = row_count

    def note_oracle_spend(self, usd: float) -> None:
        """The oracle's spend, believed. Recording it is what lets an abort commit
        the KNOWN charge instead of conservatively footing the whole held reserve."""
        self._known_judge_spend_usd = usd
        self._actual_spend_usd += usd

    def oracle_started(self) -> None:
        """A paid oracle is now in flight and may burn money it never reports."""
        self._oracle_started = True

    def oracle_finished(self) -> None:
        """It is not in flight any more -- its cost is settled and accounted."""
        self._oracle_started = False

    # -------------------------------------------------------------- unwind

    def unwind(self) -> None:
        """Release only when nothing was sent; otherwise commit the right charge.

        ``judge_started`` means "a PAID oracle had begun scoring", not merely
        "control flow reached the score call" -- it selects the conservative
        over-charge rules (full active reserve when staged; ``max(actual,
        campaign_cap)`` when not), which exist only because a paid judge may
        have burned money it never got to report. It is computed HERE from the
        two facts this object already owns, rather than trusted from a caller:
        a free oracle's abort booking ``max(actual, campaign_cap)`` eats the
        whole phase budget for a campaign that spent cents.
        """
        staged = self._staged
        reservation_id = self._reservation_id
        held_judge_reservation_id = self._held_judge_reservation_id
        active_stage = self._active_stage
        judge_started = self._oracle_started and self._oracle_is_paid
        known_judge_spend_usd = self._known_judge_spend_usd
        active_reserve_usd = self._active_reserve_usd
        answer_spend_usd = self._answer_spend_usd
        actual_spend_usd = self._actual_spend_usd
        campaign_cap_usd = self._campaign_cap_usd
        has_answer_rows = self._answer_row_count > 0
        try:
            if reservation_id is None:
                return
            if staged:
                if active_stage == "judge" and not judge_started:
                    self._release_fn(reservation_id)
                else:
                    charge = (
                        known_judge_spend_usd
                        if active_stage == "judge" and known_judge_spend_usd is not None
                        else (
                            active_reserve_usd
                            if judge_started or active_stage == "judge_canary"
                            else answer_spend_usd
                        )
                    )
                    self._commit_fn(reservation_id, charge)
            elif has_answer_rows:
                charge = (
                    max(actual_spend_usd, campaign_cap_usd) if judge_started else actual_spend_usd
                )
                self._commit_fn(reservation_id, charge)
            else:
                self._release_fn(reservation_id)
        finally:
            if held_judge_reservation_id is not None:
                self._release_fn(held_judge_reservation_id)
