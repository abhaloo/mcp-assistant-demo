"""The answer matrix: run every cell, and refuse to keep going when it stops adding up.

Extracted from ``Campaign.run_attempt`` (P1 / H2 of the 2026-08-02 review). Five
closures in that method -- ``execute_cell``, ``validate_row``, ``enforce_cell_ceiling``,
``handle_row``, ``handle_batch`` -- closed over the SAME state tuple: the task, the
spend staging, the frozen ceilings and forecasts, and the accumulating row list. Five
functions over one identical state tuple is an object, and writing it as one makes the
per-cell ceiling and the batch guards testable without constructing a whole
``Campaign``.

BEHAVIOUR IS UNCHANGED, DELIBERATELY. This is a move, not a redesign: the same calls
happen in the same order, including ``task.cells()`` being called at ``prepare()``
rather than at construction, because ``run_attempt`` calls it AFTER the canary and the
answer reservation and thirty-odd tests assert that ordering byte for byte.

THE GUARD THAT IS EASY TO MISREAD
=================================
``forecast_fits`` checks the RUNNING TOTAL against the reservation, so one runaway cell
can eat an arbitrary share of the answer reserve as long as the total still fits.
``enforce_ceiling`` is the per-cell half of that, and the two are not redundant.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.experiments.core.errors import RagExperimentError
from app.experiments.core.money import BUDGET_EPSILON_USD
from app.experiments.core.protocol import TaskAdapter
from app.experiments.core.work_matrix import (
    Cell,
    ProviderConcurrencyLimits,
    deterministic_order,
    forecast_fits,
    run_sliding_window,
)

__all__ = ["AnswerMatrix"]

_FORECAST_MESSAGE = "remaining answer-cell cost forecast no longer fits reservation"


def _row_cell(row: dict[str, Any]) -> Cell:
    return Cell(
        case_id=str(row.get("case_id")),
        arm_name=str(row.get("arm")),
        repeat=int(row.get("repeat", -1)),
    )


class AnswerMatrix:
    """One attempt's answer cells, their rows, and the guards that gate spending more.

    Constructed BEFORE ``prepare()`` so that ``Campaign``'s progress emitter can read
    ``rows`` and ``total`` from the first payload onward -- they are ``[]`` and ``0``
    until the matrix is prepared, which is exactly what an emit before the matrix
    should report.
    """

    def __init__(
        self,
        *,
        task: TaskAdapter,
        staging: Any,
        spend_stages: dict[str, Any] | None,
        max_workers: int,
        provider_limits: ProviderConcurrencyLimits | None = None,
        cell_provider_fn: Callable[[Cell], str | None] | None = None,
    ) -> None:
        self._task = task
        self._staging = staging
        self._spend_stages = spend_stages
        self._max_workers = max_workers
        self._provider_limits = provider_limits
        self._cell_provider_fn = cell_provider_fn
        ceilings = spend_stages.get("answer_cell_ceiling_usd") if spend_stages else None
        self._cell_ceilings = ceilings or {}
        self._forecasts = spend_stages["answer_cell_forecast_usd"] if spend_stages else {}
        self._cells: list[Cell] = []
        self.expected_cells: set[Cell] = set()
        self.total = 0
        self.rows: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ lifecycle

    def prepare(self) -> None:
        """Ask the adapter for its cells. Called after reservations are open,
        not at construction."""
        self._cells = list(self._task.cells())
        self.expected_cells = set(self._cells)
        self.total = len(self._cells)

    def order(self, seed: Any) -> None:
        if seed is None:
            return
        self._cells = deterministic_order(self._cells, seed=int(seed), label="answers")

    # ------------------------------------------------------------- per-cell guards

    def execute_cell(self, cell: Cell) -> dict[str, Any]:
        row = self._task.execute(cell)
        row["operational_failure"] = self._task.classify_failure(row)
        return row

    def validate_row(self, row: dict[str, Any], *, stage: str, label: str) -> None:
        """``stage`` is the machine token the adapter reasons about
        (``protocol.EVIDENCE_STAGES``); ``label`` is the human phrase in the error.
        They are separate because the error strings are pinned by tests and by
        operators reading a failed attempt, while the stage vocabulary has to stay a
        small closed set the adapters can switch on."""
        failures = self._task.evidence_failures(row, stage=stage)
        if failures:
            raise RagExperimentError(
                f"{label} evidence failed for {row.get('arm')}/{row.get('case_id')}: "
                + ", ".join(sorted(set(failures)))
            )

    def enforce_ceiling(self, row: dict[str, Any]) -> None:
        """Hard per-cell cost ceiling -- the frozen ``answer_cell_ceiling_usd``.

        RAG and SQL each enforce the ceiling in their own adapter; routing publishes it
        and enforced nothing. Enforcing here gives every adapter the guard instead of
        each re-deriving it.

        Deliberately NOT routed through ``TaskAdapter.evidence_failures``: routing's
        ``evidence_failures`` is per-route pass/fail, and a non-empty list there aborts
        the whole sweep on the first broken route -- turning a cost guard into a
        correctness guard for a task that expects broken routes.

        A ``spend_stages`` without ceilings enforces nothing (nothing frozen to
        enforce); a ceilings table missing the arm that just ran is a frozen contract
        that does not describe the matrix, and fails closed.

        Rows already stopped as ``operational_failure=budget_exceeded`` or
        ``budget_clarify`` skip the hard abort: the run budget already ended the cell;
        aborting the campaign on the same spend wastes the rest of the matrix
        (Contract A D8). Spend is still noted by the caller.
        """
        if row.get("operational_failure") in ("budget_exceeded", "budget_clarify"):
            return
        if not self._cell_ceilings:
            return
        arm_name = str(row.get("arm"))
        if arm_name not in self._cell_ceilings:
            raise RagExperimentError(f"no frozen per-cell cost ceiling for arm {arm_name!r}")
        cost_usd = float(row.get("cost_usd") or 0.0)
        ceiling_usd = float(self._cell_ceilings[arm_name])
        # Fail CLOSED on a non-finite cost: `NaN > x` is False, so a bare comparison
        # would wave it through (same trap work_matrix.forecast_fits documents).
        if not math.isfinite(cost_usd) or cost_usd > ceiling_usd + BUDGET_EPSILON_USD:
            raise RagExperimentError(
                f"answer cell cost {cost_usd} exceeds the frozen per-cell ceiling "
                f"{ceiling_usd} for {arm_name}/{row.get('case_id')}"
            )

    # ------------------------------------------------------------------- execution

    def run_probe_cells(self) -> None:
        """The pilot cells, run first and checked hardest.

        Each is popped out of the remaining work so the main sweep does not re-run it.
        """
        for probe in self._task.probe_cells():
            probe_index = self._cells.index(probe)
            probe_row = self.execute_cell(self._cells.pop(probe_index))
            self.rows.append(probe_row)
            self._staging.note_cell_spend(float(probe_row.get("cost_usd") or 0.0))
            self.validate_row(probe_row, stage="probe", label="provenance pilot")
            self.enforce_ceiling(probe_row)
        self._task.enforce_operational_budget(self.rows)

    def check_forecast(self) -> None:
        if not forecast_fits(
            self._staging.answer_spend_usd,
            self._cells,
            self._forecasts,
            self._staging.active_reserve_usd,
        ):
            raise RagExperimentError(_FORECAST_MESSAGE)

    def run_staged(self, *, on_row: Callable[[dict[str, Any]], None]) -> None:
        """Sliding-window execution with forecast guards after each completion."""

        def handle_row(row: dict[str, Any]) -> None:
            self.rows.append(row)
            self._staging.note_cell_spend(float(row.get("cost_usd") or 0.0))
            on_row(row)

        def handle_checkpoint(
            completed_row: dict[str, Any],
            remaining_cells: list[Cell],
        ) -> None:
            self.validate_row(completed_row, stage="batch", label="answer batch")
            self.enforce_ceiling(completed_row)
            self._task.enforce_operational_budget(self.rows)
            if not forecast_fits(
                self._staging.answer_spend_usd,
                remaining_cells,
                self._forecasts,
                self._staging.active_reserve_usd,
            ):
                raise RagExperimentError(_FORECAST_MESSAGE)

        run_sliding_window(
            self._cells,
            execute_fn=self.execute_cell,
            max_workers=self._max_workers,
            cell_provider_fn=self._cell_provider_fn,
            provider_limits=self._provider_limits,
            on_row_complete=handle_row,
            on_checkpoint=handle_checkpoint,
        )

    def run_unstaged(self, *, on_row: Callable[[dict[str, Any]], None]) -> None:
        """No spend stages, so no per-batch budget guards to run between batches."""
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = [pool.submit(self.execute_cell, cell) for cell in self._cells]
            for future in as_completed(futures):
                row = future.result()
                self.rows.append(row)
                self._staging.note_cell_spend(float(row.get("cost_usd") or 0.0))
                on_row(row)

    # -------------------------------------------------------------------- the rows

    def sort(self) -> None:
        self.rows.sort(key=lambda row: (row["case_id"], row["arm"], row["repeat"]))

    def replace_rows(self, rows: list[dict[str, Any]]) -> None:
        """Swap in a settled row list, then re-sort. Used by the deferred-evidence
        stage, which is allowed to revise rows and is the only thing that may."""
        self.rows = list(rows)
        self.sort()

    def verify_complete(self) -> None:
        observed = [_row_cell(row) for row in self.rows]
        if len(observed) != len(set(observed)) or set(observed) != self.expected_cells:
            raise RagExperimentError("pre-judge answer matrix is incomplete or duplicated")

    def validate_final(self) -> None:
        for row in self.rows:
            self.validate_row(row, stage="final", label="pre-judge")

    def total_cost_usd(self) -> float:
        return sum(float(row.get("cost_usd") or 0.0) for row in self.rows)
