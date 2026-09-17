import pytest

from app.eval.run_stats import (
    classify_moves,
    discordant_counts,
    family_cluster_paired_bootstrap_ci,
    mcnemar_exact_p,
    paired_bootstrap_ci,
    verdict,
)


def test_mcnemar_no_discordant_pairs_is_p1():
    assert mcnemar_exact_p(0, 0) == 1.0


def test_mcnemar_all_one_direction_small():
    # 5 improvements, 0 regressions: n=5, two-sided p = 2 * (0.5**5) = 0.0625
    assert mcnemar_exact_p(0, 5) == pytest.approx(0.0625, abs=1e-6)


def test_mcnemar_worked_example_12():
    # n=12, k=1: 2 * (C(12,0)+C(12,1)) / 2**12 = 2 * 13/4096
    assert mcnemar_exact_p(1, 11) == pytest.approx(0.0063477, abs=1e-6)


def test_mcnemar_balanced_caps_at_one():
    assert mcnemar_exact_p(6, 6) == 1.0


def test_discordant_counts_binarizes_at_threshold():
    pairs = [
        ("a", 1.0, 0.0),  # base pass, cand fail -> b
        ("b", 0.0, 1.0),  # base fail, cand pass -> c
        ("c", 1.0, 1.0),  # concordant pass
        ("d", 0.4, 0.6),  # crosses 0.5 -> c
        ("e", 0.6, 0.4),  # crosses 0.5 -> b
    ]
    assert discordant_counts(pairs) == (2, 2)


def test_classify_moves_splits_regressions_and_improvements():
    pairs = [("a", 1.0, 0.0), ("b", 0.0, 1.0), ("c", 1.0, 1.0)]
    moves = classify_moves(pairs)
    assert [m[0] for m in moves["regressions"]] == ["a"]
    assert [m[0] for m in moves["improvements"]] == ["b"]


def test_bootstrap_ci_constant_deltas_is_degenerate_interval():
    res = paired_bootstrap_ci([0.25, 0.25, 0.25])
    assert res["point"] == pytest.approx(0.25)
    assert res["lo"] == pytest.approx(0.25)
    assert res["hi"] == pytest.approx(0.25)
    assert res["n"] == 3


def test_bootstrap_ci_empty_is_zero():
    assert paired_bootstrap_ci([]) == {"point": 0.0, "lo": 0.0, "hi": 0.0, "n": 0}


def test_bootstrap_ci_is_deterministic_under_seed():
    deltas = [0.1, -0.2, 0.3, 0.0, 0.5]
    a = paired_bootstrap_ci(deltas, seed=7)
    b = paired_bootstrap_ci(deltas, seed=7)
    assert a == b
    assert a["lo"] <= a["point"] <= a["hi"]


def test_bootstrap_ci_exact_seeded_output_is_pinned():
    # Golden-master pin for the shared percentile-bootstrap core: these exact numbers
    # were computed against the pre-extraction implementation. Extracting the core
    # shared with family_cluster_paired_bootstrap_ci must not move a single digit.
    deltas = [0.1, -0.2, 0.3, 0.0, 0.5]
    result = paired_bootstrap_ci(deltas, seed=7)
    assert result == {
        "point": 0.13999999999999999,
        "lo": -0.06000000000000001,
        "hi": 0.33999999999999997,
        "n": 5,
    }


def test_family_cluster_bootstrap_exact_seeded_output_is_pinned():
    # Golden-master pin, same purpose as test_bootstrap_ci_exact_seeded_output_is_pinned
    # above but for the family-clustered variant of the shared bootstrap core.
    observations = [
        ("family-a", 0.0, 1.0),
        ("family-a", 0.0, 0.5),
        ("family-b", 1.0, 0.0),
        ("family-b", 1.0, 0.6),
        ("family-c", 0.2, 0.9),
    ]
    result = family_cluster_paired_bootstrap_ci(observations, seed=7)
    assert result == {
        "point": 0.25,
        "lo": -0.6999999999999998,
        "hi": 0.75,
        "n_families": 3,
        "n_cases": 5,
    }


def test_verdict_three_states():
    assert verdict(0.01, 0.01) == "SIGNIFICANT_IMPROVEMENT"
    assert verdict(0.0, 0.20) == "NO_REGRESSION_NOT_SIGNIFICANT"
    assert verdict(-0.05, 0.01) == "REGRESSION_RISK"


def test_family_cluster_bootstrap_resamples_intent_families_not_paraphrases():
    observations = [
        ("family-a", 0.0, 1.0),
        ("family-a", 0.0, 1.0),
        ("family-a", 0.0, 1.0),
        ("family-a", 0.0, 1.0),
        ("family-b", 1.0, 0.0),
        ("family-b", 1.0, 0.0),
        ("family-b", 1.0, 0.0),
        ("family-b", 1.0, 0.0),
    ]

    result = family_cluster_paired_bootstrap_ci(observations, n_boot=200, seed=11)

    assert result["point"] == pytest.approx(0.0)
    assert result["n_families"] == 2
    assert result["n_cases"] == 8
    assert result["lo"] <= -1.0
    assert result["hi"] >= 1.0
