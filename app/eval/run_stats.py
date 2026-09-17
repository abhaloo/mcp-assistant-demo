"""Pure paired-comparison statistics for two eval runs (baseline vs candidate).

No file I/O, no domain knowledge — inputs are paired per-case pass_rates, outputs are
numbers. The CLI (scripts/eval/analyze_sql_runs.py) owns loading and the degenerate-gold
carve-out; this module owns the math so it can be unit-tested in isolation.

Gate doctrine (lite-research 2026-06-28): a continuous paired bootstrap CI on the
exec-match delta AND a binary exact-McNemar significance test on the same cases. Declare a
win only when the CI lower bound is strictly above zero AND McNemar p < alpha. With few
repeats/cases this deliberately refuses to bless 1-2pp deltas — that conservatism is the
point on a small eval set.
"""

from __future__ import annotations

import random
from math import comb

# pair = (case_id, base_pass_rate, cand_pass_rate)
Pair = tuple[str, float, float]
FamilyPair = tuple[str, float, float]


def discordant_counts(pairs: list[Pair], threshold: float = 0.5) -> tuple[int, int]:
    """McNemar 2x2 off-diagonal: (b, c) = (base-pass & cand-fail, base-fail & cand-pass)."""
    b = c = 0
    for _cid, base, cand in pairs:
        base_pass, cand_pass = base >= threshold, cand >= threshold
        if base_pass and not cand_pass:
            b += 1
        elif cand_pass and not base_pass:
            c += 1
    return b, c


def mcnemar_exact_p(b: int, c: int) -> float:
    """Two-sided exact-binomial McNemar p-value for discordant counts b, c.

    Under H0 each discordant pair is a fair coin. Exact (not chi-squared) because small
    evals almost always have b+c < 25, where the normal approximation is unreliable.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) * (0.5**n)
    return min(1.0, 2.0 * tail)


def _percentile_bootstrap(
    values: list[float], *, n_boot: int, seed: int, alpha: float
) -> dict[str, float]:
    """Shared core: percentile bootstrap CI of the mean of `values`. Seeded -> reproducible.

    Both paired_bootstrap_ci (per-case deltas) and family_cluster_paired_bootstrap_ci
    (per-family means) resample-and-average in exactly this way; only what they resample
    differs. Callers handle their own empty-input shape, since the two functions return
    different key sets for that case.
    """
    n = len(values)
    point = sum(values) / n
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        boots.append(sum(sample) / n)
    boots.sort()
    lo = boots[int((alpha / 2) * n_boot)]
    hi = boots[int((1 - alpha / 2) * n_boot)]
    return {"point": point, "lo": lo, "hi": hi}


def paired_bootstrap_ci(
    deltas: list[float], *, n_boot: int = 10000, seed: int = 7, alpha: float = 0.05
) -> dict:
    """Percentile bootstrap CI of the mean per-case delta. Seeded -> reproducible."""
    n = len(deltas)
    if n == 0:
        return {"point": 0.0, "lo": 0.0, "hi": 0.0, "n": 0}
    result = _percentile_bootstrap(deltas, n_boot=n_boot, seed=seed, alpha=alpha)
    return {**result, "n": n}


def family_cluster_paired_bootstrap_ci(
    observations: list[FamilyPair],
    *,
    n_boot: int = 10000,
    seed: int = 7,
    alpha: float = 0.05,
) -> dict[str, float | int]:
    """Bootstrap candidate-minus-baseline accuracy by intent-family cluster."""
    families: dict[str, list[float]] = {}
    for family_id, baseline, candidate in observations:
        families.setdefault(family_id, []).append(candidate - baseline)

    family_ids = sorted(families)
    n_families = len(family_ids)
    n_cases = len(observations)
    if not family_ids:
        return {
            "point": 0.0,
            "lo": 0.0,
            "hi": 0.0,
            "n_families": 0,
            "n_cases": 0,
        }

    family_means = [sum(families[family_id]) / len(families[family_id]) for family_id in family_ids]
    result = _percentile_bootstrap(family_means, n_boot=n_boot, seed=seed, alpha=alpha)
    return {**result, "n_families": n_families, "n_cases": n_cases}


def classify_moves(pairs: list[Pair], threshold: float = 0.5) -> dict:
    """Split discordant cases into regressions (lost) and improvements (gained)."""
    regressions, improvements = [], []
    for cid, base, cand in pairs:
        base_pass, cand_pass = base >= threshold, cand >= threshold
        if base_pass and not cand_pass:
            regressions.append((cid, base, cand))
        elif cand_pass and not base_pass:
            improvements.append((cid, base, cand))
    return {"regressions": regressions, "improvements": improvements}


def verdict(ci_lo: float, p_value: float, *, alpha: float = 0.05) -> str:
    """Both-gates rule: significant only if CI strictly above 0 AND McNemar p < alpha."""
    if ci_lo > 0 and p_value < alpha:
        return "SIGNIFICANT_IMPROVEMENT"
    if ci_lo >= 0:
        return "NO_REGRESSION_NOT_SIGNIFICANT"
    return "REGRESSION_RISK"
