"""Task-agnostic experiment orchestrator: spend staging, work matrix, oracle scoring."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.experiments.core.answer_matrix import AnswerMatrix
from app.experiments.core.answer_persistence import sanitize_answer_rows_for_persistence
from app.experiments.core.attempt_store import AttemptStore
from app.experiments.core.checkpoint import seal_judge_checkpoint
from app.experiments.core.money import require_finite_nonnegative_usd
from app.experiments.core.protocol import (
    CellProvidingTask,
    Oracle,
    PreflightOracle,
    SettlingTaskAdapter,
    TaskAdapter,
)
from app.experiments.core.spend_plan import spend_plan_for
from app.experiments.core.spend_staging import SpendStaging
from app.experiments.core.stage_receipts import write_stage_receipt
from app.experiments.core.termination import write_termination_evidence
from app.experiments.core.work_matrix import (
    DEFAULT_MAX_WORKERS,
    ProviderConcurrencyLimits,
    _default_provider_limits,
)

__all__ = ["Campaign", "CampaignAttemptResult"]


@dataclass(frozen=True)
class CampaignAttemptResult:
    """Paths and verdict produced by one append-only attempt."""

    attempt_dir: Path
    verdict: Any
    actual_spend_usd: float


def _failure_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Operational failures so far, by reason.

    ``classify_failure`` returns a reason string or ``None``, so the truthiness test
    is the classification: a row with no reason is not a failure. Rebuilt per emit
    rather than kept as a running counter -- at a few hundred cells the whole sweep
    costs microseconds, and a counter is state that can disagree with the rows it
    claims to describe.
    """
    counts: dict[str, int] = {}
    for row in rows:
        reason = row.get("operational_failure")
        if reason:
            counts[str(reason)] = counts.get(str(reason), 0) + 1
    return counts


class Campaign:
    """Drive a TaskAdapter through one immutable, budget-accounted attempt."""

    def __init__(
        self,
        *,
        task: TaskAdapter,
        reserve_fn: Callable[[float], str],
        commit_fn: Callable[[str, float], None],
        release_fn: Callable[[str], None],
        attempt_root: Path,
        max_workers: int = DEFAULT_MAX_WORKERS,
        provider_limits: ProviderConcurrencyLimits | None = None,
        progress_fn: Callable[[dict[str, Any]], None] | None = None,
        runtime_invariant_fn: Callable[[str], None] | None = None,
        reconcile_ledger: Any | None = None,
    ) -> None:
        self._task = task
        self._staging = SpendStaging(
            reserve_fn=reserve_fn, commit_fn=commit_fn, release_fn=release_fn
        )
        self._attempt_root = attempt_root
        self._max_workers = max_workers
        self._provider_limits = provider_limits or _default_provider_limits(max_workers)
        self._progress_fn = progress_fn or (lambda _progress: None)
        self._runtime_invariant_fn = runtime_invariant_fn or (lambda _stage: None)
        self._reconcile_ledger = reconcile_ledger

    def run_attempt(self, *, attempt_id: str) -> CampaignAttemptResult:
        """Run one new attempt; an existing attempt id is never resumed or overwritten."""
        task = self._task
        oracle: Oracle = task.oracle()
        task.verify_frozen_contract()
        frozen_inputs = task.freeze_inputs()
        spend_stages = frozen_inputs.get("spend_stages")
        # spend_plan_for refuses a PAID oracle without a canary preflight seam here,
        # before the attempt directory exists and long before anything can reserve --
        # exactly what the canary stage exists to prevent it from bypassing.
        plan = spend_plan_for(spend_stages, oracle)
        preflight_oracle = oracle if isinstance(oracle, PreflightOracle) else None

        store = AttemptStore(self._attempt_root, attempt_id)
        store.write_json(
            "attempt.json",
            {
                "schema_version": 1,
                "attempt_id": attempt_id,
                "parent_attempt": None,
                "freeze_hash": frozen_inputs["freeze_hash"],
                "runtime": frozen_inputs["runtime"],
            },
        )

        staging = self._staging
        # Every value ``unwind()`` decides from is now set here, once, before the try.
        # ``oracle.is_paid`` in particular: reading it late -- inside the except
        # handler -- would let a missing attribute replace the original exception and
        # skip the unwind entirely, leaking the live reservation.
        staging.begin_attempt(
            staged=bool(spend_stages),
            campaign_cap_usd=float(frozen_inputs["campaign_cap_usd"]),
            oracle_is_paid=bool(oracle.is_paid),
        )
        matrix = AnswerMatrix(
            task=task,
            staging=staging,
            spend_stages=spend_stages,
            max_workers=self._max_workers,
            provider_limits=self._provider_limits,
            cell_provider_fn=task.cell_provider if isinstance(task, CellProvidingTask) else None,
        )

        def emit(stage: str, **extra: Any) -> None:
            """Publish one progress payload; spend/failure keys read at emit time."""
            payload: dict[str, Any] = {
                "stage": stage,
                "completed": len(matrix.rows),
                "total": matrix.total,
                "spend_usd": staging.actual_spend_usd,
                "reserved_usd": staging.active_reserve_usd,
                "cap_usd": staging.campaign_cap_usd,
                "failure_counts": _failure_counts(matrix.rows),
                # Explicit None off answer stage — heartbeat state accumulates via update().
                "arm": None,
            }
            payload.update(extra)
            self._progress_fn(payload)

        try:
            if preflight_oracle is not None:
                preflight_oracle.reset_for_attempt()
            self._runtime_invariant_fn("before_canary")
            plan.open_answer_stage(
                staging,
                oracle=preflight_oracle,
                frozen_inputs=frozen_inputs,
                runtime_invariant_fn=self._runtime_invariant_fn,
            )

            matrix.prepare()
            emit("answers")

            plan.run_answers(
                matrix,
                frozen_inputs=frozen_inputs,
                on_row=lambda row: emit("answers", arm=row.get("arm")),
            )

            matrix.sort()

            # Settle deferred evidence (e.g. OpenRouter /generation) before final checks.
            if isinstance(task, SettlingTaskAdapter):
                matrix.replace_rows(task.settle_rows(matrix.rows))
                # Provider-reported costs replace estimates; re-sum before commit.
                staging.resettle_answers(
                    total_usd=matrix.total_cost_usd(),
                    row_count=len(matrix.rows),
                )

            matrix.verify_complete()
            matrix.validate_final()

            plan.settle_answers(staging)

            self._runtime_invariant_fn("before_judge")
            task.verify_frozen_contract()
            store.write_jsonl(
                "answers.jsonl",
                sanitize_answer_rows_for_persistence(matrix.rows),
            )
            emit("judge")
            plan.open_oracle_stage(staging, oracle=preflight_oracle)
            staging.oracle_started()
            try:
                scored_rows, oracle_validation = oracle.score(matrix.rows)
            except Exception as oracle_exc:
                partial_spend = getattr(oracle_exc, "actual_spend_usd", None)
                try:
                    partial_spend_usd = float(partial_spend)
                except (TypeError, ValueError):
                    partial_spend_usd = math.nan
                if math.isfinite(partial_spend_usd) and partial_spend_usd >= 0:
                    staging.note_oracle_spend(partial_spend_usd)
                raise
            oracle_cost_usd = float(oracle_validation.details.get("cost_usd") or 0.0)
            oracle_cost_usd = require_finite_nonnegative_usd(
                oracle_cost_usd, what="judge returned invalid cost evidence"
            )
            staging.note_oracle_spend(oracle_cost_usd)
            plan.settle_oracle(staging, oracle_cost_usd)

            checkpoint = seal_judge_checkpoint(
                store=store,
                scored_rows=scored_rows,
                oracle_validation=oracle_validation,
                identity=task.checkpoint_identity(),
                spend_evidence=staging.checkpoint_spend_evidence(),
            )
            write_stage_receipt(
                store=store,
                stage="judge_checkpoint_sealed",
                inputs=checkpoint.input_hashes,
                outputs=checkpoint.output_hashes,
                spend=checkpoint.spend_evidence,
            )

            self._runtime_invariant_fn("before_verdict")
            verdict = task.finalize(
                scored_rows=scored_rows,
                oracle_validation=oracle_validation,
                actual_spend_usd=staging.actual_spend_usd,
                store=store,
            )
            store.hash_artifacts()
            plan.settle_final(staging)
            try:
                emit("complete", status=getattr(verdict, "status", None))
            except Exception:  # noqa: BLE001 - a status line is not the attempt's outcome
                # The attempt is done here: artifacts written, hashes taken, money
                # settled. A progress_fn that raises on this last line (in the runners
                # it calls ``write_heartbeat``, which propagates by design) would
                # otherwise re-report a completed, paid-for attempt to its caller as a
                # failure. The money was always safe -- ``unwind()`` returns
                # immediately with no live reservation -- but the report is what the
                # caller acts on.
                pass
            return CampaignAttemptResult(
                attempt_dir=store.dir,
                verdict=verdict,
                actual_spend_usd=staging.actual_spend_usd,
            )
        except Exception as exc:
            # The artifact writes are diagnostics; the unwind is the ledger. Either
            # write can raise (real PermissionError [WinError 5] on Windows atomic
            # writes), and before this `finally` that left the live reservation
            # neither committed nor released -- leaking the phase's whole budget to
            # save a debug file. A write failure still propagates, with the original
            # campaign failure chained onto it as __context__.
            #
            # emit("error", ...) runs INSIDE this try, ahead of the `finally`, for the
            # same reason: a progress_fn that raises here must not strand the live
            # reservation just to publish a status line. CampaignConsole cannot raise,
            # but progress_fn is caller-supplied, and this is the one call site where a
            # raise costs money rather than a line.
            try:
                emit("error", error=f"{type(exc).__name__}: {exc}")
                if not store.exists("attempt_error.json"):
                    store.write_json(
                        "attempt_error.json",
                        {"error": f"{type(exc).__name__}: {exc}"},
                    )
                if matrix.rows and not store.exists("answers.partial.jsonl"):
                    store.write_jsonl(
                        "answers.partial.jsonl",
                        sanitize_answer_rows_for_persistence(matrix.rows),
                    )
                write_termination_evidence(
                    store=store,
                    staging=staging,
                    reconcile_ledger=self._reconcile_ledger,
                )
            finally:
                # Zero arguments, deliberately. Every value ``unwind()`` needs was set
                # through the staging API on the way in, by the code that knew it;
                # there is nothing for the handler to reassemble, and so nothing for it
                # to reassemble WRONG. The eleven-argument version had to be handed the
                # state from eleven locals, at the one point in the method where a
                # stale one is unrecoverable.
                staging.unwind()
            raise
