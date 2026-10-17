"""Offline SQL eval gate for the DeepSeek routing experiment.

Mid-stage ``SQL_DEV_PASS`` (smoke+dev) unlocks holdout via a hashed artifact
receipt. Full ``SQL-ELIGIBLE`` requires smoke, unlocked holdout run, and quality.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, NamedTuple

from app.eval.run_stats import paired_bootstrap_ci
from app.experiments.holdout_unlock import (
    assert_holdout_unlocked,
    is_holdout_unlocked,
    issue_sql_dev_pass,
)


class PassRatePair(NamedTuple):
    """One case's baseline/candidate exec-match pass rate.

    A NamedTuple, not a plain tuple alias: still index-accessible (``pair[1]``)
    for existing callers and still JSON-serialises as a list, but also gives
    ``.case_id`` / ``.baseline`` / ``.candidate`` to new callers.
    """

    case_id: str
    baseline: float
    candidate: float


DEFAULT_REGRESSION_TOLERANCE = 0.05
DEFAULT_INFRA_HOLD_THRESHOLD = 0.02
DEFAULT_PASS_THRESHOLD = 0.5
DEFAULT_INVALID_SQL_DELTA_MAX = 0.10
DEFAULT_COST_RATIO_MAX = 0.5
BOOTSTRAP_SEED = 7


@dataclass(frozen=True)
class SqlGateResult:
    status: str
    reasons: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unmeasured_cost_reason(
    cost_usd_baseline_luna: float | None,
    cost_usd_candidate: float | None,
) -> str | None:
    """Name the arm(s) that produced no price observation at all, or ``None``.

    A cost of ``None`` means no row on that arm reported provider token usage --
    routine, not exotic: ``app/eval/sql/diagnostics.py`` records usage metadata as
    "often absent for the tool-calling agent". It is NOT zero, and the ledger's
    ``cost_usd_accounted`` is not a stand-in: fail-closed ceiling charges run ~8x the
    priced mean, so folding them into a ``candidate <= 0.5 x baseline`` ratio moves the
    economics verdict by the infra noise instead of by the model.

    The wording is deliberate. This is a measurement gap, and the operator reading the
    verdict must not file it as "the candidate is worse".
    """
    missing = [
        role
        for role, value in (
            ("baseline", cost_usd_baseline_luna),
            ("candidate", cost_usd_candidate),
        )
        if value is None
    ]
    if not missing:
        return None
    where = "either arm" if len(missing) == 2 else f"the {missing[0]} arm"
    return (
        f"cost could not be measured on {where}: no row there reported provider token "
        "usage, so the cost ratio check could not run - inconclusive economics, "
        "not a quality regression"
    )


def _case_flips(
    pairs: list[PassRatePair],
    *,
    threshold: float = DEFAULT_PASS_THRESHOLD,
) -> list[tuple[str, float, float]]:
    return [
        (cid, base, cand) for cid, base, cand in pairs if base >= threshold and cand < threshold
    ]


def _quality_reasons(
    pass_rate_pairs: list[PassRatePair],
    *,
    invalid_sql_rate_baseline: float,
    invalid_sql_rate_candidate: float,
    safety_failures: int,
    cost_usd_baseline_luna: float | None,
    cost_usd_candidate: float | None,
    regression_tolerance: float,
    invalid_sql_delta_max: float,
    cost_ratio_max: float,
    pass_threshold: float,
    bootstrap_seed: int,
    details: dict[str, Any],
    cost_ratio_calibrated: bool = True,
) -> list[str]:
    reasons: list[str] = []
    deltas = [cand - base for _cid, base, cand in pass_rate_pairs]
    ci = paired_bootstrap_ci(deltas, seed=bootstrap_seed)
    details["pass_rate"] = {
        "n": ci["n"],
        "mean_delta": round(ci["point"], 4),
        "ci_lo": round(ci["lo"], 4),
        "ci_hi": round(ci["hi"], 4),
    }
    if ci["lo"] < -regression_tolerance:
        reasons.append(
            f"pass rate paired 95% CI lower bound {ci['lo']:.4f} < -{regression_tolerance:.2f}"
        )

    flips = _case_flips(pass_rate_pairs, threshold=pass_threshold)
    details["case_flips"] = [f[0] for f in flips]
    if flips:
        flip_ids = ", ".join(f[0] for f in flips)
        reasons.append(f"case flip(s) base≥{pass_threshold} → cand<{pass_threshold}: {flip_ids}")

    invalid_delta = round(invalid_sql_rate_candidate - invalid_sql_rate_baseline, 10)
    if invalid_delta >= invalid_sql_delta_max:
        reasons.append(f"invalid SQL rate delta {invalid_delta:.4f} >= {invalid_sql_delta_max:.2f}")

    if safety_failures != 0:
        reasons.append(f"safety failures {safety_failures} != 0")

    if not cost_ratio_calibrated:
        return reasons

    if cost_usd_baseline_luna is None or cost_usd_candidate is None:
        # An unmeasured arm has no cost to compare. Comparing anyway raises TypeError
        # from inside the gate -- and the gate runs AFTER the matrix has been paid for,
        # so the run dies with the reservation committed and no verdict on disk. The
        # callers turn this into a HOLD with an explicit reason; here it is simply not
        # a quality reason, so it must not be one.
        return reasons

    cost_cap = cost_ratio_max * cost_usd_baseline_luna
    if cost_usd_candidate > cost_cap:
        reasons.append(
            f"candidate cost ${cost_usd_candidate:.4f} "
            f"> {cost_ratio_max:.0%} × Luna ${cost_usd_baseline_luna:.4f} "
            f"(cap ${cost_cap:.4f})"
        )
    return reasons


def evaluate_sql_gate(
    pass_rate_pairs: list[PassRatePair],
    *,
    stage: Literal["dev", "full"] = "full",
    invalid_sql_rate_baseline: float,
    invalid_sql_rate_candidate: float,
    safety_failures: int,
    cost_usd_baseline_luna: float | None,
    cost_usd_candidate: float | None,
    infra_failure_rate: float,
    smoke_passed: bool,
    dev_passed: bool = True,
    holdout_run: bool = True,
    regression_tolerance: float = DEFAULT_REGRESSION_TOLERANCE,
    infra_hold_threshold: float = DEFAULT_INFRA_HOLD_THRESHOLD,
    invalid_sql_delta_max: float = DEFAULT_INVALID_SQL_DELTA_MAX,
    cost_ratio_max: float = DEFAULT_COST_RATIO_MAX,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    bootstrap_seed: int = BOOTSTRAP_SEED,
    write_receipt: bool = False,
    cost_ratio_calibrated: bool = True,
) -> SqlGateResult:
    """Decide SQL gate status (SQL_DEV_PASS / SQL-ELIGIBLE / HOLD / FAIL) from offline aggregates.

    stage="dev": Smoke+dev mid-gate. Emits SQL_DEV_PASS (never SQL-ELIGIBLE).
                 When write_receipt=True and pass, issues unlock receipt.
    stage="full": Full eligibility. Requires smoke + unlocked holdout run.
                  Emits SQL-ELIGIBLE on pass.
    """
    if stage not in ("dev", "full"):
        raise ValueError(f"unknown SQL eval gate stage: {stage!r}")

    stages_dict: dict[str, Any] = {"smoke_passed": smoke_passed, "dev_passed": dev_passed}
    if stage == "full":
        stages_dict["holdout_run"] = holdout_run

    details: dict[str, Any] = {
        "infra_failure_rate": infra_failure_rate,
        "invalid_sql_delta": round(invalid_sql_rate_candidate - invalid_sql_rate_baseline, 4),
        "cost_usd_baseline_luna": cost_usd_baseline_luna,
        "cost_usd_candidate": cost_usd_candidate,
        "stages": stages_dict,
    }

    if infra_failure_rate > infra_hold_threshold:
        return SqlGateResult(
            status="HOLD",
            reasons=[f"infra failure rate {infra_failure_rate:.4f} > {infra_hold_threshold:.2f}"],
            details=details,
        )

    reasons: list[str] = []
    if not smoke_passed:
        reasons.append("smoke gate has not passed")

    if stage == "full":
        if not dev_passed:
            reasons.append("dev gate has not passed")
        if not holdout_run:
            reasons.append("holdout evaluation has not been run")
        elif not is_holdout_unlocked():
            reasons.append("holdout unlock receipt missing or artifact hash mismatch")

    reasons.extend(
        _quality_reasons(
            pass_rate_pairs,
            invalid_sql_rate_baseline=invalid_sql_rate_baseline,
            invalid_sql_rate_candidate=invalid_sql_rate_candidate,
            safety_failures=safety_failures,
            cost_usd_baseline_luna=cost_usd_baseline_luna,
            cost_usd_candidate=cost_usd_candidate,
            regression_tolerance=regression_tolerance,
            invalid_sql_delta_max=invalid_sql_delta_max,
            cost_ratio_max=cost_ratio_max,
            pass_threshold=pass_threshold,
            bootstrap_seed=bootstrap_seed,
            details=details,
            cost_ratio_calibrated=cost_ratio_calibrated,
        )
    )

    if reasons:
        return SqlGateResult(status="FAIL", reasons=reasons, details=details)

    if not cost_ratio_calibrated:
        return SqlGateResult(
            status="HOLD",
            reasons=["cost ratio gate not calibrated for this baseline profile"],
            details=details,
        )

    # After the quality reasons on purpose: a real regression is a FAIL on its own
    # evidence and must not be softened into HOLD just because the prices went missing.
    # Reaching here means quality was fine and only the economics are unknown.
    cost_reason = _unmeasured_cost_reason(cost_usd_baseline_luna, cost_usd_candidate)
    if cost_reason is not None:
        return SqlGateResult(status="HOLD", reasons=[cost_reason], details=details)

    if stage == "dev":
        result = SqlGateResult(status="SQL_DEV_PASS", reasons=[], details=details)
        if write_receipt:
            issue_sql_dev_pass(result.to_dict())
        return result

    # stage == "full"
    # Fail-closed: never emit SQL-ELIGIBLE without a verified unlock receipt.
    assert_holdout_unlocked()
    return SqlGateResult(status="SQL-ELIGIBLE", reasons=[], details=details)
