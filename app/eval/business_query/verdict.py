"""Sealed release-gate verdict for the Business Query holdout comparison.

run_stats.py is deliberately domain-free (see its own docstring): pure paired-comparison
math, no knowledge of what is being compared. This module is the one place that owns the
Business Query release gate, so the verdict is a single sealed computation over raw
per-case observations rather than caller-supplied scalars that could be assembled
inconsistently (mismatched estimator levels, a bare regression flag nothing computes).

Frozen thresholds live in evals/experiments/business-query-module-success-contract.json
`acceptance`; every constant below names the field it mirrors. They are module constants,
not function parameters, so a caller cannot loosen the gate by passing a different number.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple

from app.eval.business_query.artifacts import assert_acceptance_artifacts_posted
from app.eval.business_query.contract import (
    ArmIdentities,
    EqualityKeySet,
    RunKind,
    assert_comparable_arms,
    assert_run_kind_legal,
)
from app.eval.run_stats import family_cluster_paired_bootstrap_ci

# acceptance.holdout_delta_percentage_points_min (10 percentage points, expressed as a
# 0..1 fraction to match family_cluster_paired_bootstrap_ci's output units).
_UPLIFT_THRESHOLD_POINT = 0.10

# Minimum intent-family clusters before the percentile bootstrap is trusted at all. The
# frozen suite declares family_count: 20; below half that, an interval is reported but
# not trusted for a release decision.
_MIN_FAMILY_CLUSTERS = 10

# acceptance.domain_slice_regression_percentage_points_max
_DOMAIN_REGRESSION_PP_MAX = 5.0

# "a domain slice under 20 cases" (acceptance.small_slice_additional_misses_max applies below this)
_SMALL_SLICE_CASE_CEILING = 20

# acceptance.small_slice_additional_misses_max
_SMALL_SLICE_ADDITIONAL_MISSES_MAX = 1

# acceptance.family_stratum_accuracy_spread_percentage_points_max
_FAMILY_STRATUM_SPREAD_PP_MAX = 5.0

# Binarization point for "did this case pass", consistent with run_stats.discordant_counts.
_PASS_THRESHOLD = 0.5


class ObservedCase(NamedTuple):
    """One paired case-level observation feeding the release gate."""

    family_id: str
    case_id: str
    domain: str
    baseline_pass_rate: float
    candidate_pass_rate: float


@dataclass(frozen=True)
class DomainRegression:
    """A single domain that failed the domain-slice regression check."""

    domain: str
    n_cases: int
    delta_percentage_points: float
    additional_misses: int
    small_slice: bool


Verdict = Literal[
    "SIGNIFICANT_UPLIFT",
    "HOLD_INSUFFICIENT_CLUSTERS",
    "HOLD_REGRESSION",
    "HOLD_BELOW_10PP",
    "HOLD_INTERVAL_NOT_ABOVE_ZERO",
]

RegressionColumn = Literal["domain_slice_regression", "family_stratum_accuracy_spread"]


@dataclass(frozen=True)
class UpliftVerdict:
    """Frozen outcome of the Business Query holdout release gate."""

    verdict: Verdict
    point: float
    ci_lo: float
    ci_hi: float
    n_families: int
    n_cases: int
    failed_regression_columns: tuple[RegressionColumn, ...]
    domain_regressions: tuple[DomainRegression, ...]
    family_stratum_accuracy_spread_pp: float


def _domain_regressions(observations: Sequence[ObservedCase]) -> tuple[DomainRegression, ...]:
    """Evaluate the domain-slice regression column, once per distinct domain.

    Domains at or above the small-slice ceiling are judged on aggregate accuracy delta;
    smaller domains use an absolute additional-misses count instead, because a handful of
    cases makes a percentage-point delta noisy (one flipped case can be 10+ points).
    """
    by_domain: dict[str, list[ObservedCase]] = defaultdict(list)
    for case in observations:
        by_domain[case.domain].append(case)

    violations: list[DomainRegression] = []
    for domain in sorted(by_domain):
        cases = by_domain[domain]
        n = len(cases)
        baseline_acc = sum(c.baseline_pass_rate for c in cases) / n
        candidate_acc = sum(c.candidate_pass_rate for c in cases) / n
        delta_pp = (candidate_acc - baseline_acc) * 100
        additional_misses = sum(
            1
            for c in cases
            if c.baseline_pass_rate >= _PASS_THRESHOLD and c.candidate_pass_rate < _PASS_THRESHOLD
        )
        small_slice = n < _SMALL_SLICE_CASE_CEILING
        failed = (
            additional_misses > _SMALL_SLICE_ADDITIONAL_MISSES_MAX
            if small_slice
            else delta_pp < -_DOMAIN_REGRESSION_PP_MAX
        )
        if failed:
            violations.append(
                DomainRegression(
                    domain=domain,
                    n_cases=n,
                    delta_percentage_points=delta_pp,
                    additional_misses=additional_misses,
                    small_slice=small_slice,
                )
            )
    return tuple(violations)


def _family_stratum_accuracy_spread_pp(observations: Sequence[ObservedCase]) -> float:
    """Worst within-family candidate-accuracy spread across phrasings, in percentage points."""
    by_family: dict[str, list[float]] = defaultdict(list)
    for case in observations:
        by_family[case.family_id].append(case.candidate_pass_rate)
    if not by_family:
        return 0.0
    return max((max(rates) - min(rates)) * 100 for rates in by_family.values())


def evaluate_uplift(observations: Sequence[ObservedCase]) -> UpliftVerdict:
    """Apply the frozen Business Query release gate to paired case observations.

    The family-clustered point estimate and 95% CI come from
    run_stats.family_cluster_paired_bootstrap_ci (not reimplemented here); both frozen
    regression columns and the minimum-cluster guard are computed from the same
    observations. Callers supply only raw per-case data - there is no scalar entry point.
    """
    cluster = family_cluster_paired_bootstrap_ci(
        [(c.family_id, c.baseline_pass_rate, c.candidate_pass_rate) for c in observations]
    )
    n_families = int(cluster["n_families"])
    n_cases = int(cluster["n_cases"])
    point, ci_lo, ci_hi = cluster["point"], cluster["lo"], cluster["hi"]

    if n_families < _MIN_FAMILY_CLUSTERS:
        return UpliftVerdict(
            verdict="HOLD_INSUFFICIENT_CLUSTERS",
            point=point,
            ci_lo=ci_lo,
            ci_hi=ci_hi,
            n_families=n_families,
            n_cases=n_cases,
            failed_regression_columns=(),
            domain_regressions=(),
            family_stratum_accuracy_spread_pp=0.0,
        )

    domain_violations = _domain_regressions(observations)
    spread_pp = _family_stratum_accuracy_spread_pp(observations)

    failed_columns: list[RegressionColumn] = []
    if domain_violations:
        failed_columns.append("domain_slice_regression")
    if spread_pp > _FAMILY_STRATUM_SPREAD_PP_MAX:
        failed_columns.append("family_stratum_accuracy_spread")

    verdict: Verdict
    if failed_columns:
        verdict = "HOLD_REGRESSION"
    elif point < _UPLIFT_THRESHOLD_POINT:
        verdict = "HOLD_BELOW_10PP"
    elif ci_lo <= 0:
        verdict = "HOLD_INTERVAL_NOT_ABOVE_ZERO"
    else:
        verdict = "SIGNIFICANT_UPLIFT"

    return UpliftVerdict(
        verdict=verdict,
        point=point,
        ci_lo=ci_lo,
        ci_hi=ci_hi,
        n_families=n_families,
        n_cases=n_cases,
        failed_regression_columns=tuple(failed_columns),
        domain_regressions=domain_violations,
        family_stratum_accuracy_spread_pp=spread_pp,
    )


def evaluate_release_uplift(
    observations: Sequence[ObservedCase],
    *,
    run_kind: RunKind,
    control: ArmIdentities,
    candidate: ArmIdentities,
    control_keys: EqualityKeySet,
    candidate_keys: EqualityKeySet,
    preregistered_treatment: frozenset[str],
    posted_manifest: Path | None,
) -> UpliftVerdict:
    """Release comparison. Smoke/diagnostic cannot mint ACCEPTED or SIGNIFICANT_UPLIFT."""
    assert_run_kind_legal(run_kind, subset=False, emit_release_verdict=True)
    assert_comparable_arms(
        control,
        candidate,
        control_keys,
        candidate_keys,
        preregistered_treatment=preregistered_treatment,
    )
    assert_acceptance_artifacts_posted(run_kind=run_kind, posted_manifest=posted_manifest)
    return evaluate_uplift(observations)
