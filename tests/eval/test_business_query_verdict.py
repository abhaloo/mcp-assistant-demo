import json

import pytest

from app.eval.business_query.verdict import ObservedCase, evaluate_uplift


def _uniform_families(n: int, *, baseline: float, candidate: float, cases_per_family: int = 1):
    """n families, each with `cases_per_family` cases, all with the same pass rates."""
    cases = []
    for i in range(n):
        for j in range(cases_per_family):
            cases.append(
                ObservedCase(
                    family_id=f"fam-{i}",
                    case_id=f"fam-{i}-{j}",
                    domain="core",
                    baseline_pass_rate=baseline,
                    candidate_pass_rate=candidate,
                )
            )
    return cases


def test_significant_uplift_requires_clusters_point_and_positive_interval():
    observations = _uniform_families(10, baseline=0.0, candidate=1.0, cases_per_family=2)

    result = evaluate_uplift(observations)

    assert result.verdict == "SIGNIFICANT_UPLIFT"
    assert result.point == pytest.approx(1.0)
    assert result.ci_lo == pytest.approx(1.0)
    assert result.n_families == 10
    assert result.n_cases == 20
    assert result.failed_regression_columns == ()
    assert result.domain_regressions == ()


def test_hold_below_10pp_when_point_estimate_too_small():
    observations = _uniform_families(10, baseline=0.0, candidate=0.05)

    result = evaluate_uplift(observations)

    assert result.verdict == "HOLD_BELOW_10PP"
    assert result.point == pytest.approx(0.05)
    assert result.failed_regression_columns == ()


def test_hold_interval_not_above_zero_when_ci_spans_zero_despite_high_point():
    # 10 families that win big (+1.0 delta) plus 5 that lose big (-1.0 delta), all in one
    # >=20-case domain so the aggregate domain delta (+60pp) clears the regression bar even
    # though the family-level split makes the bootstrap interval span zero.
    observations = _uniform_families(10, baseline=0.0, candidate=1.0, cases_per_family=2)
    for i in range(5):
        observations.append(
            ObservedCase(
                family_id=f"fam-bad-{i}",
                case_id=f"fam-bad-{i}-a",
                domain="core",
                baseline_pass_rate=1.0,
                candidate_pass_rate=0.0,
            )
        )

    result = evaluate_uplift(observations)

    assert result.verdict == "HOLD_INTERVAL_NOT_ABOVE_ZERO"
    assert result.n_families == 15
    assert result.point >= 0.10
    assert result.ci_lo <= 0
    assert result.failed_regression_columns == ()
    assert result.domain_regressions == ()


def test_hold_regression_when_only_domain_slice_column_fails():
    # 10 clean families (2 cases each, domain "core", n=20 -> percentage-point rule, no
    # regression) plus one dedicated small family entirely in domain "risky" (n=3 -> under
    # the small-slice ceiling, 3 additional misses > the max of 1). Every phrasing within
    # "fam-risky" fails identically, so the family-spread column stays at 0 - only the
    # domain-slice column can be responsible for the HOLD.
    observations = _uniform_families(10, baseline=0.0, candidate=1.0, cases_per_family=2)
    for j in range(3):
        observations.append(
            ObservedCase(
                family_id="fam-risky",
                case_id=f"fam-risky-{j}",
                domain="risky",
                baseline_pass_rate=1.0,
                candidate_pass_rate=0.0,
            )
        )

    result = evaluate_uplift(observations)

    assert result.verdict == "HOLD_REGRESSION"
    assert result.failed_regression_columns == ("domain_slice_regression",)
    assert [d.domain for d in result.domain_regressions] == ["risky"]
    assert result.domain_regressions[0].additional_misses == 3
    assert result.domain_regressions[0].small_slice is True
    assert result.family_stratum_accuracy_spread_pp == pytest.approx(0.0)
    # Without the domain check this dataset would otherwise clear every other bar.
    assert result.point >= 0.10
    assert result.ci_lo > 0


def test_hold_regression_when_only_family_stratum_spread_column_fails():
    # 9 clean families (domain "core", uniform candidate=1.0) plus one family whose 4
    # phrasings disagree (3 pass, 1 fails): a 100pp within-family spread, well over the
    # 5pp max. All cases keep baseline=0.0 so the domain-level aggregate never regresses -
    # only the spread column can be responsible for the HOLD.
    observations = _uniform_families(9, baseline=0.0, candidate=1.0, cases_per_family=2)
    for j, candidate in enumerate([1.0, 1.0, 1.0, 0.0]):
        observations.append(
            ObservedCase(
                family_id="fam-spread",
                case_id=f"fam-spread-{j}",
                domain="core",
                baseline_pass_rate=0.0,
                candidate_pass_rate=candidate,
            )
        )

    result = evaluate_uplift(observations)

    assert result.verdict == "HOLD_REGRESSION"
    assert result.failed_regression_columns == ("family_stratum_accuracy_spread",)
    assert result.domain_regressions == ()
    assert result.family_stratum_accuracy_spread_pp == pytest.approx(100.0)
    # Without the spread check this dataset would otherwise clear every other bar.
    assert result.point >= 0.10
    assert result.ci_lo > 0


def test_hold_regression_reports_both_columns_when_both_fail():
    observations = _uniform_families(10, baseline=0.0, candidate=1.0, cases_per_family=2)
    for j in range(3):
        observations.append(
            ObservedCase(
                family_id="fam-risky",
                case_id=f"fam-risky-{j}",
                domain="risky",
                baseline_pass_rate=1.0,
                candidate_pass_rate=0.0,
            )
        )
    for j, candidate in enumerate([1.0, 1.0, 1.0, 0.0]):
        observations.append(
            ObservedCase(
                family_id="fam-spread",
                case_id=f"fam-spread-{j}",
                domain="core",
                baseline_pass_rate=0.0,
                candidate_pass_rate=candidate,
            )
        )

    result = evaluate_uplift(observations)

    assert result.verdict == "HOLD_REGRESSION"
    assert set(result.failed_regression_columns) == {
        "domain_slice_regression",
        "family_stratum_accuracy_spread",
    }


def test_hold_insufficient_clusters_below_minimum_family_count():
    # Every other statistic here would otherwise pass cleanly (point=1.0, ci_lo=1.0) -
    # the cluster-count guard must still fire first.
    observations = _uniform_families(9, baseline=0.0, candidate=1.0)

    result = evaluate_uplift(observations)

    assert result.verdict == "HOLD_INSUFFICIENT_CLUSTERS"
    assert result.n_families == 9
    assert result.failed_regression_columns == ()
    assert result.domain_regressions == ()


def test_empty_observations_hold_insufficient_clusters():
    result = evaluate_uplift([])

    assert result.verdict == "HOLD_INSUFFICIENT_CLUSTERS"
    assert result.n_families == 0
    assert result.n_cases == 0


def _eq():
    from app.eval.business_query.contract import SHARED_EQUALITY_KEYS, EqualityKeySet

    return EqualityKeySet({key: "a" * 64 for key in SHARED_EQUALITY_KEYS})


def _arm(arm_id: str, *, events: bool = True, **overrides):
    from app.eval.business_query.contract import PER_ARM_IDENTITIES, ArmIdentities

    values = {key: f"{arm_id}-{key}" for key in PER_ARM_IDENTITIES}
    values.update(overrides)
    return ArmIdentities(values=values, arm_id=arm_id, executor_events_complete=events)


def test_diagnostic_cannot_emit_significant_uplift_as_release(tmp_path):
    from app.eval.business_query.contract import RunKindError
    from app.eval.business_query.verdict import evaluate_release_uplift

    observations = _uniform_families(10, baseline=0.0, candidate=1.0, cases_per_family=2)
    posted = tmp_path / "MANIFEST.json"
    posted.write_text(
        json.dumps(
            {
                "posted": True,
                "summary_sha256": "a" * 64,
                "verdict_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RunKindError, match="diagnostic cannot emit a release verdict"):
        evaluate_release_uplift(
            observations,
            run_kind="diagnostic",
            control=_arm("module-control"),
            candidate=_arm("module-candidate"),
            control_keys=_eq(),
            candidate_keys=_eq(),
            preregistered_treatment=frozenset({"prompt_hash", "protocol_id"}),
            posted_manifest=posted,
        )


def test_release_uplift_rejects_missing_executor_events(tmp_path):
    from app.eval.business_query.contract import ComparisonStructurallyInvalid
    from app.eval.business_query.verdict import evaluate_release_uplift

    observations = _uniform_families(10, baseline=0.0, candidate=1.0, cases_per_family=2)

    with pytest.raises(ComparisonStructurallyInvalid, match="without a resolvable executor event"):
        evaluate_release_uplift(
            observations,
            run_kind="gate",
            control=_arm("module-control", events=False),
            candidate=_arm("module-candidate"),
            control_keys=_eq(),
            candidate_keys=_eq(),
            preregistered_treatment=frozenset({"prompt_hash", "protocol_id"}),
            posted_manifest=tmp_path / "MANIFEST.json",
        )
