"""Live SQL campaign wiring: snapshot, gold receipt, budget, spend ledger."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from app.eval.sql.snapshot import (
    gold_verification_receipt,
    point_agent_at_snapshot,
    snapshot_identity,
)
from app.experiments.arm_profiles import (
    SqlCampaignConfig,
    build_sql_pairwise_campaign,
    load_arm_profiles,
)
from app.experiments.arming import require_armed
from app.experiments.console import CampaignConsole
from app.experiments.core.campaign import Campaign, CampaignAttemptResult
from app.experiments.core.checkpoint import maybe_raise_recoverable_checkpoint
from app.experiments.core.money import (
    estimate_cost_usd,
    round_reservation_usd,
)
from app.experiments.core.progress_heartbeat import (
    CoalescedHeartbeatWriter,
    write_progress_heartbeat,
)
from app.experiments.core.runtime_identity import RuntimeIdentity
from app.experiments.holdout_unlock import load_holdout_cases
from app.experiments.live_errors import LiveSqlEvalError
from app.experiments.manifest import (
    DEEPSEEK_MODEL,
    EXPERIMENT_ID,
    SQL_DEV_PATH,
    load_manifest,
    verify_manifest,
)
from app.experiments.rag_freeze import current_runtime_identity
from app.experiments.review.writer import write_attempt_review
from app.experiments.spend_coordinator import (
    PHASE_BUDGETS,
    acquire,
    commit,
    release,
)
from app.experiments.sql_arm_canary import (
    SqlArmCanaryReceipt,
    run_sql_arm_canaries,
)
from app.experiments.sql_ceiling_calibration import (
    calibrate_cell_ceiling_by_arm,
    calibration_manifest_hash,
)
from app.experiments.sql_snapshot_receipt import (
    SnapshotReceipt,
    SnapshotVerifyConfig,
    verify_materialized_snapshot,
)
from app.experiments.tasks.sql_task import (
    FrozenSqlExperiment,
    SqlArm,
    SqlOracle,
    SqlTask,
    freeze_sql_experiment,
)

__all__ = [
    "LEDGER_MODEL_ID",
    "ROUTER_POLICY",
    "SQL_ARMS",
    "SQL_BASELINE_PROFILE_ID",
    "SQL_CANDIDATE_PROFILE_ID",
    "SQL_LIVE_ENV",
    "DEEPSEEK_DIRECT_SQL_LIVE_ENV",
    "SQL_REPEATS",
    "STOCK_SQL_HARD_CAP_USD",
    "LiveSqlEvalError",
    "default_sql_campaign",
    "freeze_sql_stage",
    "load_sql_cases",
    "project_sql_budget_usd",
    "require_sql_live_armed",
    "require_deepseek_direct_sql_live_armed",
    "run_live_sql_eval",
    "run_sql_exact_wire_canaries",
    "snapshot_identity",
    "verify_sql_snapshot_readiness",
]

SQL_LIVE_ENV = "DEEPSEEK_SQL_LIVE"
DEEPSEEK_DIRECT_SQL_LIVE_ENV = "DEEPSEEK_DIRECT_SQL_LIVE"

BASELINE_ARM = SqlArm(name="forced-luna", chat_deployment="gpt-5.6-luna")
# reasoning_effort explicit, not the ambient default (fail-closed on OpenRouter
# + controls=None). "low" preserves this campaign's existing observed behavior.
CANDIDATE_ARM = SqlArm(
    name="forced-deepseek", chat_deployment=DEEPSEEK_MODEL, reasoning_effort="low"
)
SQL_ARMS: tuple[SqlArm, ...] = (BASELINE_ARM, CANDIDATE_ARM)
SQL_BASELINE_PROFILE_ID: str | None = "prod-luna"
SQL_CANDIDATE_PROFILE_ID: str | None = "deepseek-low"

# prod-router replayed over the two frozen arms, for $0. `escalated_arm` is luna
# because the production predicate escalates the hard-financial pattern to the
# escalation deployment, which is what the forced-luna arm runs.
ROUTER_POLICY: dict[str, Any] = {
    "name": "prod-router",
    "predicate": "is_hard_financial",
    "default_arm": CANDIDATE_ARM.name,
    "escalated_arm": BASELINE_ARM.name,
}

SQL_REPEATS = 5
SQL_STAGE_PHASE = {"dev": "sql_dev", "holdout": "sql_holdout"}
SQL_STAGE_CONCURRENCY = {"dev": 4, "holdout": 8}
# The ledger prices reservations against one model id; the baseline arm is the more
# expensive of the two, so reserving at its rate never under-reserves.
LEDGER_MODEL_ID = BASELINE_ARM.chat_deployment

# Measured, not guessed: 44 cases x 5 repeats in
# evals/runs/2026-07-07-sql-citations-worktree/summary.json came to 1_488_232 prompt
# and 50_807 completion tokens over 220 agent runs -> ~6.8k / ~231 per run. Rounded up.
EST_TOKENS_PER_RUN = {"prompt": 7_000, "completion": 300}
# A hard per-cell ceiling well above the mean (a retrying agent legitimately costs
# several times a clean run) but low enough that a runaway loop trips the evidence
# check instead of quietly eating the reservation.
CELL_CEILING_FACTOR = 12.0
# Finish-first floor for stock freeze/CLI. Canary calibration may still use
# CANARY_HARD_CAP_USD=0.02 as an *upper* clamp on the canary formula, but
# projected ceilings always floor at this stock value so a cheap trusted-canary
# cannot freeze a sub-stock matrix ceiling. A max-effort + clarify cell can cost
# more than $0.025, so the floor must clear that or finish-first aborts a
# legitimately expensive cell.
STOCK_SQL_HARD_CAP_USD = 0.05
# Observed per-cell cost can run roughly double the naive forecast; the factor
# must cover that margin so a reservation is not aborted while campaign budget
# remains unused.
CELL_FORECAST_FACTOR = 4.0
RESERVE_SAFETY_FACTOR = 1.25


def default_sql_campaign() -> SqlCampaignConfig:
    """Default prod-luna vs deepseek-low SQL pairwise campaign."""
    return build_sql_pairwise_campaign("prod-luna", "deepseek-low")


def require_sql_live_armed() -> None:
    require_armed(SQL_LIVE_ENV, purpose="live SQL campaign")


def require_deepseek_direct_sql_live_armed() -> None:
    require_armed(DEEPSEEK_DIRECT_SQL_LIVE_ENV, purpose="live DeepSeek-direct SQL")


def load_sql_cases(stage: str) -> list[dict[str, Any]]:
    """The only loader for either stage's cases.

    ``dev`` is manifest-verified (count + sha256). ``holdout`` goes through
    ``holdout_unlock.load_holdout_cases()``, which raises ``PermissionError`` unless a
    receipt exists whose hash matches its gate artifact.
    """
    if stage == "holdout":
        return load_holdout_cases()
    if stage != "dev":
        raise LiveSqlEvalError(f"unknown SQL stage: {stage!r}")
    verify_manifest(load_manifest())
    return [
        json.loads(line)
        for line in SQL_DEV_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def resolve_sql_cases(
    stage: str,
    *,
    case_ids: Sequence[str] | None = None,
    clarify_answer_overrides: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Load stage cases, optionally restricted to an allowlist (flip canary)."""
    from app.experiments.sql_case_allowlist import (
        apply_clarify_answer_overrides,
        select_sql_cases_by_ids,
    )

    cases = load_sql_cases(stage)
    if case_ids is not None:
        try:
            cases = select_sql_cases_by_ids(cases, case_ids)
        except ValueError as exc:
            raise LiveSqlEvalError(str(exc)) from exc
    if clarify_answer_overrides:
        cases = apply_clarify_answer_overrides(cases, clarify_answer_overrides)
    return cases


def project_sql_budget_usd(
    *,
    n_cases: int,
    arms: tuple[SqlArm, ...],
    repeats: int,
    campaign_cap_usd: float,
    canary_calibrations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-cell ceilings/forecasts and the answer reservation, priced from prices.yaml.

    Fails closed on an unpriced arm deployment: better to refuse at projection time
    than to discover mid-matrix that every row charges its ceiling.
    """
    per_cell: dict[str, float] = {}
    for arm in arms:
        deployment = arm.chat_deployment or LEDGER_MODEL_ID
        try:
            per_cell[arm.name] = float(
                estimate_cost_usd(
                    deployment,
                    EST_TOKENS_PER_RUN["prompt"],
                    EST_TOKENS_PER_RUN["completion"],
                )
            )
        except KeyError as exc:
            raise LiveSqlEvalError(
                f"arm {arm.name!r} deployment {deployment!r} is unpriced: {exc}"
            ) from exc

    ceilings = {
        name: round_reservation_usd(value * CELL_CEILING_FACTOR) for name, value in per_cell.items()
    }
    if canary_calibrations:
        for arm_name, calibration in canary_calibrations.items():
            ceilings[arm_name] = round_reservation_usd(
                max(float(calibration.ceiling_usd), STOCK_SQL_HARD_CAP_USD)
            )
    else:
        # Finish-first: stock freeze/CLI without canary receipts.
        ceilings = {
            name: round_reservation_usd(max(value, STOCK_SQL_HARD_CAP_USD))
            for name, value in ceilings.items()
        }
    forecasts = {
        name: round_reservation_usd(value * CELL_FORECAST_FACTOR)
        for name, value in per_cell.items()
    }
    cells_per_arm = n_cases * repeats
    answer_reserve_usd = round_reservation_usd(
        sum(forecasts.values()) * cells_per_arm * RESERVE_SAFETY_FACTOR
    )
    return {
        "est_cell_usd": per_cell,
        "answer_cell_ceiling_usd": ceilings,
        "answer_cell_forecast_usd": forecasts,
        "answer_reserve_usd": answer_reserve_usd,
        "campaign_cap_usd": campaign_cap_usd,
        "n_cells": cells_per_arm * len(arms),
        "canary_calibration_hash": (
            calibration_manifest_hash(canary_calibrations) if canary_calibrations else None
        ),
    }


def verify_sql_snapshot_readiness(
    *,
    config: SnapshotVerifyConfig,
    connect_fn: Callable[[SnapshotVerifyConfig], dict[str, Any]],
) -> SnapshotReceipt:
    """Seal a materialized snapshot readiness receipt before SQL LLM spend."""
    return verify_materialized_snapshot(config, connect_fn=connect_fn)


def run_sql_exact_wire_canaries(
    *,
    arms: tuple[SqlArm, ...],
    case: dict[str, Any],
    run_case_fn: Callable[..., dict[str, Any]],
    reserve_fn: Callable[[float], Any] | None = None,
) -> tuple[list[SqlArmCanaryReceipt], dict[str, Any]]:
    """Run exact-wire canaries and derive receipt-bound ceilings."""
    receipts = run_sql_arm_canaries(
        arms,
        case,
        run_case_fn=run_case_fn,
        reserve_fn=reserve_fn,
    )
    calibrations = calibrate_cell_ceiling_by_arm(receipts)
    return receipts, calibrations


@dataclass(frozen=True)
class SqlStageFreezeRequest:
    """Inputs to freeze_sql_stage."""

    stage: str
    output_path: Path
    runtime: RuntimeIdentity | None = None
    gold_verification_fn: Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]] | None = None
    campaign: SqlCampaignConfig | None = None
    arms: tuple[SqlArm, ...] | None = None
    repeats: int = SQL_REPEATS
    canary_calibrations: dict[str, Any] | None = None
    snapshot_receipt: SnapshotReceipt | None = None
    case_ids: Sequence[str] | None = None
    clarify_answer_overrides: dict[str, str] | None = None


def freeze_sql_stage(
    request: SqlStageFreezeRequest | None = None,
    *,
    stage: str | None = None,
    output_path: Path | None = None,
    runtime: RuntimeIdentity | None = None,
    gold_verification_fn: (
        Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]] | None
    ) = None,
    campaign: SqlCampaignConfig | None = None,
    arms: tuple[SqlArm, ...] | None = None,
    repeats: int = SQL_REPEATS,
    canary_calibrations: dict[str, Any] | None = None,
    snapshot_receipt: SnapshotReceipt | None = None,
    case_ids: Sequence[str] | None = None,
    clarify_answer_overrides: dict[str, str] | None = None,
) -> FrozenSqlExperiment:
    """Freeze one stage's cases, gold receipt, arms and budgets before any spend."""
    if request is not None:
        req = request
    else:
        if stage is None or output_path is None:
            raise ValueError("stage and output_path are required")
        req = SqlStageFreezeRequest(
            stage=stage,
            output_path=output_path,
            runtime=runtime,
            gold_verification_fn=gold_verification_fn,
            campaign=campaign,
            arms=arms,
            repeats=repeats,
            canary_calibrations=canary_calibrations,
            snapshot_receipt=snapshot_receipt,
            case_ids=case_ids,
            clarify_answer_overrides=clarify_answer_overrides,
        )

    resolved_campaign = req.campaign or default_sql_campaign()
    resolved_arms = req.arms if req.arms is not None else resolved_campaign.arms
    resolved_router_policy = dict(resolved_campaign.router_policy)
    resolved_cap = resolved_campaign.campaign_cap_usd
    resolved_runtime = req.runtime or current_runtime_identity()
    if resolved_runtime.dirty:
        raise LiveSqlEvalError("dirty runtime: commit and push exact code before freezing")
    # Loaded FIRST: for holdout this is the receipt gate, and it must fire before the
    # snapshot is touched or a budget is projected.
    cases = resolve_sql_cases(
        req.stage,
        case_ids=req.case_ids,
        clarify_answer_overrides=req.clarify_answer_overrides,
    )
    identity = snapshot_identity()

    # MUST precede gold verification. app/eval/sql/agent/agent.py resolves its module
    # global `engine` lazily, on first use (app.eval.sql.agent.billing_engine.get_engine()
    # -- the LIVE billing tunnel -- if nothing else has set it first), and build_sql_database
    # reads that module global. point_agent_at_snapshot is the only thing that rebinds
    # it to the frozen restore and attaches the read-only guard + the SET TIMESTAMP
    # clock pin. Verifying gold before this call would measure the wrong database on an
    # unpinned clock, which voids the degeneracy receipt: a gold that is degenerate on
    # the snapshot need not be on prod, so coincidental_match_suspected would never
    # fire. scripts/eval/evaluate_sql_agent.py::main has always pinned before verify_gold;
    # this matches it. Idempotent -- run_live_sql_eval re-pins at run concurrency.
    point_agent_at_snapshot(1)

    verify_fn = req.gold_verification_fn or gold_verification_receipt
    gold_verification = verify_fn(cases)
    failed = sorted(case_id for case_id, entry in gold_verification.items() if entry.get("error"))
    missing = sorted({str(case["id"]) for case in cases} - set(gold_verification))
    if failed or missing:
        raise LiveSqlEvalError(f"gold verification failed: errored={failed} missing={missing}")

    projection = project_sql_budget_usd(
        n_cases=len(cases),
        arms=resolved_arms,
        repeats=req.repeats,
        campaign_cap_usd=resolved_cap,
        canary_calibrations=req.canary_calibrations,
    )
    phase = SQL_STAGE_PHASE[req.stage]
    if projection["answer_reserve_usd"] > projection["campaign_cap_usd"]:
        raise LiveSqlEvalError(
            f"projected reservation ${projection['answer_reserve_usd']:.4f} exceeds the "
            f"${projection['campaign_cap_usd']:.2f} campaign cap"
        )
    if projection["campaign_cap_usd"] > PHASE_BUDGETS[phase]:
        raise LiveSqlEvalError(
            f"campaign cap ${projection['campaign_cap_usd']:.2f} exceeds the {phase!r} "
            f"phase budget ${PHASE_BUDGETS[phase]:.2f}"
        )

    registry = load_arm_profiles()
    baseline_profile = registry[resolved_campaign.baseline_id]
    candidate_profile = registry[resolved_campaign.candidate_id]
    arm_profile_projections = {
        baseline_profile.arm_name: baseline_profile.freeze_projection(),
        candidate_profile.arm_name: candidate_profile.freeze_projection(),
    }

    return freeze_sql_experiment(
        experiment_id=f"{EXPERIMENT_ID}-sql-{req.stage}",
        stage=req.stage,
        cases=cases,
        gold_verification=gold_verification,
        arms=resolved_arms,
        baseline_arm=resolved_arms[0].name,
        candidate_arm=resolved_arms[-1].name,
        runtime=resolved_runtime,
        output_path=req.output_path,
        created_at=datetime.now(UTC).isoformat(),
        snapshot={
            **identity,
            "projection": projection,
            "snapshot_receipt": (
                req.snapshot_receipt.to_dict() if req.snapshot_receipt is not None else None
            ),
        },
        repeats=req.repeats,
        campaign_cap_usd=projection["campaign_cap_usd"],
        spend_stages={
            "answer_reserve_usd": projection["answer_reserve_usd"],
            "answer_cell_ceiling_usd": projection["answer_cell_ceiling_usd"],
            "answer_cell_forecast_usd": projection["answer_cell_forecast_usd"],
        },
        router_policy=resolved_router_policy,
        allowed_actual_models=(
            dict(resolved_campaign.allowed_actual_models)
            if resolved_campaign.allowed_actual_models is not None
            else None
        ),
        arm_profile_projections=arm_profile_projections,
    )


def run_live_sql_eval(
    *,
    frozen: FrozenSqlExperiment,
    attempt_id: str,
    ledger_path: Path | None = None,
    max_workers: int | None = None,
    issue_unlock_receipt: bool = False,
    console: CampaignConsole | None = None,
) -> CampaignAttemptResult:
    """Reserve the stage's phase, run the frozen matrix, and gate. Arms real spend."""
    require_sql_live_armed()
    stage = frozen.document["stage"]
    phase = SQL_STAGE_PHASE[stage]
    if stage == "holdout":
        # Belt and braces: the freeze already went through the receipt gate, but a
        # frozen holdout contract could be replayed later, and the receipt could have
        # been revoked since.
        load_sql_cases("holdout")
    concurrency = max_workers or SQL_STAGE_CONCURRENCY[stage]
    point_agent_at_snapshot(concurrency)

    from app.eval.sql.diagnostics import _fetch, run_case
    from app.eval.sql.gold_cache import build_gold_cache, install_gold_cache

    install_gold_cache(build_gold_cache(frozen.document["cases"], fetch_fn=_fetch))

    heartbeat_path = frozen.output_path.parent / "heartbeat.json"
    heartbeat_lock = Lock()
    coalesced_heartbeat = CoalescedHeartbeatWriter(
        heartbeat_path,
        write_fn=write_progress_heartbeat,
    )

    def write_heartbeat(progress: dict[str, Any]) -> None:
        with heartbeat_lock:
            coalesced_heartbeat.update(
                progress,
                extra={"attempt_id": attempt_id, "stage_name": stage},
            )

    view = console if console is not None else CampaignConsole.create(mode="off")
    # Console first, heartbeat second. The console cannot raise, so it cannot starve the
    # heartbeat; ``write_heartbeat`` keeps propagating exactly as it did before the
    # console existed. This composition was written out by hand, identically, in both
    # runners -- ``tee`` is that closure with its reasoning stated once.
    report_progress = view.tee(write_heartbeat)

    task = SqlTask(
        frozen=frozen,
        run_case_fn=run_case,
        oracle=SqlOracle(),
        issue_unlock_receipt=issue_unlock_receipt,
    )
    campaign = Campaign(
        task=task,
        reserve_fn=lambda amount: acquire(phase, LEDGER_MODEL_ID, amount, ledger_path=ledger_path),
        commit_fn=lambda reservation, actual: commit(reservation, actual, ledger_path=ledger_path),
        release_fn=lambda reservation: release(reservation, ledger_path=ledger_path),
        attempt_root=frozen.output_path.parent / "attempts",
        max_workers=concurrency,
        progress_fn=report_progress,
    )
    try:
        result = campaign.run_attempt(attempt_id=attempt_id)
        # After run_attempt RETURNS -- see the same call in live_rag_eval for why.
        write_attempt_review(result.attempt_dir)
        return result
    except Exception:
        attempt_dir = frozen.output_path.parent / "attempts" / attempt_id
        maybe_raise_recoverable_checkpoint(attempt_dir)
        raise
    finally:
        install_gold_cache(None)
        with heartbeat_lock:
            coalesced_heartbeat.flush_best_effort()
        # Give the terminal back on every exit -- the dashboard hides the cursor, and
        # an abort that skipped this would leave the operator's next shell prompt
        # without one.
        view.close()
