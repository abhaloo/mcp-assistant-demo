"""The standards the campaign review judges against -- versioned, cited, arguable.

R1 of docs/superpowers/specs/2026-08-02-campaign-console-and-review-design.md.

WHY THIS IS A FILE AND NOT A PROMPT
===================================
A model saying "this run follows industry best practice" is worthless: it cannot be
checked, it cannot be disagreed with, and it will say it again next time whether or not
anything changed. Every criterion below is a named, versioned statement with a citation,
so the comparison can be ARGUED WITH -- by a reviewer, by a future maintainer, or by the
person whose campaign it just called UNSOUND.

Changing a threshold here changes the verdict of every future review, and the change is
a diff someone can object to. That is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bumped whenever a threshold or criterion changes. Stamped into every review so an
#: old report can be read against the standard it was actually judged by -- a verdict
#: whose criteria have silently moved is not a verdict.
CRITERIA_VERSION = "1.1.0"

#: How much a criterion's outcome weighs on the verdict.
#:
#: ``blocking`` means the run's soundness DEPENDS on this criterion: failing it forces
#: UNSOUND, and -- just as importantly -- being unable to evaluate it forces QUALIFIED,
#: because a verdict that would have rested on evidence nobody gathered is not a pass.
#: ``qualifying`` means a failure narrows what the run licenses without invalidating it.
BLOCKING = "blocking"
QUALIFYING = "qualifying"


@dataclass(frozen=True)
class Criterion:
    id: str
    statement: str
    citation: str
    #: ``BLOCKING`` or ``QUALIFYING``. Lives HERE rather than at each check's return
    #: statements: it is the single field that decides UNSOUND vs QUALIFIED, so it
    #: belongs in the versioned, cited, arguable file alongside the thresholds -- not
    #: restated two or three times per check where two branches can quietly disagree.
    severity: str
    #: What a reader may NOT conclude when this criterion is not met. The most useful
    #: field in the whole review: it turns "check failed" into a sentence about the
    #: claim you have to stop making.
    does_not_license: str


#: Below this many paired cases per arm, the paired bootstrap is resampling too few
#: distinct pairs for its interval to carry the meaning people read into it. This is a
#: CONVENTION, not a law -- stated here so it can be argued with rather than buried as
#: a literal in a comparison.
MIN_PAIRED_CASES = 30

#: Significance level for the between-arm operational-failure comparison. Matches the
#: repo's existing default (app/eval/run_stats.verdict) so the review and the gate do
#: not quietly disagree about what "significant" means.
FAILURE_BALANCE_ALPHA = 0.05

#: Share of rows whose cost was a fail-closed CEILING rather than a measured usage
#: figure. Ceilings are deliberately pessimistic, so a high share inflates the arm that
#: happened to lose its usage data and makes any cost RATIO between arms unreadable.
MAX_CEILING_COST_SHARE = 0.10

#: Tolerance when reconciling the summed row costs against the attempt's own spend
#: figure. Float addition is not associative and the two are summed in different
#: orders, so an exact match is the wrong test; anything above this is a real
#: disagreement about money, not arithmetic noise.
SPEND_RECONCILIATION_TOLERANCE_USD = 0.01

CRITERIA: dict[str, Criterion] = {
    "matrix_integrity": Criterion(
        id="matrix_integrity",
        severity=BLOCKING,
        statement=(
            "Every cell the campaign intended to run appears exactly once. A duplicated "
            "cell double-weights one case; a missing one silently changes what was compared."
        ),
        citation=(
            "Standard experimental-design requirement; enforced in-run by "
            "Campaign.run_attempt's pre-judge completeness check, re-verified here because "
            "a replayed attempt directory may have been assembled differently."
        ),
        does_not_license=(
            "You may not describe this as a comparison over the frozen case set, and any "
            "per-arm mean is over an unknown denominator."
        ),
    ),
    "arm_balance": Criterion(
        id="arm_balance",
        severity=BLOCKING,
        statement="Both arms ran the same cells, so the comparison is paired.",
        citation=(
            "Paired designs are the basis for the repo's own analysis "
            "(app/eval/run_stats.paired_bootstrap_ci, mcnemar_exact_p), both of which "
            "assume the arms share cases."
        ),
        does_not_license=(
            "You may not use the paired statistics: the difference between arms is "
            "confounded with the difference between the case sets they ran."
        ),
    ),
    "statistical_power": Criterion(
        id="statistical_power",
        severity=QUALIFYING,
        statement=(
            f"At least {MIN_PAIRED_CASES} paired cases per arm before a difference between "
            "arms is treated as measurable."
        ),
        citation=(
            "Conventional adequacy threshold for the normal approximation and for "
            "bootstrap resampling to see enough distinct pairs; see the interval itself "
            "(paired_bootstrap_ci) for the real evidence."
        ),
        does_not_license=(
            "You may not call a difference real or absent. A small sample does not show "
            "the arms are equivalent -- it shows the experiment could not tell."
        ),
    ),
    "failure_balance": Criterion(
        id="failure_balance",
        severity=QUALIFYING,
        statement=(
            "Operational failures are not concentrated in one arm. Tested with McNemar's "
            f"exact test on the paired failure outcomes at alpha={FAILURE_BALANCE_ALPHA}."
        ),
        citation=(
            "McNemar (1947), for paired binary outcomes; implemented in this repo as "
            "app/eval/run_stats.mcnemar_exact_p. Chosen over a fixed rate-difference "
            "threshold because a fixed threshold is either too strict at large n or "
            "meaningless at small n."
        ),
        does_not_license=(
            "You may not attribute the quality gap to quality. The arm that crashed more "
            "scores worse for reasons that are not about answer quality."
        ),
    ),
    "ci_excludes_zero": Criterion(
        id="ci_excludes_zero",
        severity=QUALIFYING,
        statement=(
            "Every paired confidence interval the run recorded excludes zero, so the "
            "direction of the difference between arms is established rather than assumed."
        ),
        citation=(
            "The interval is the run's own (app/eval/run_stats.paired_bootstrap_ci), read "
            "back out of verdict.json rather than recomputed -- a review that recomputed "
            "it could disagree with the gate that shipped, which is a worse failure than "
            "not checking. Stated as 'the paired delta interval contains zero' rather "
            "than the design's 'the two arms' intervals overlap': this harness computes "
            "ONE interval over per-case deltas, and for a paired design that is the "
            "stronger and more appropriate test -- two overlapping marginal intervals do "
            "not imply a non-significant paired difference."
        ),
        does_not_license=(
            "You may not say one arm is better, or quote a percentage improvement. The "
            "run is consistent with the arms being equal and with the difference running "
            "the other way."
        ),
    ),
    "spend_reconciliation": Criterion(
        id="spend_reconciliation",
        severity=QUALIFYING,
        statement=(
            "The summed per-row cost agrees with the attempt's own spend figure to within "
            f"${SPEND_RECONCILIATION_TOLERANCE_USD:.2f}."
        ),
        citation=(
            "Basic accounting reconciliation. A cost model that does not reconcile with "
            "itself cannot support any per-case or per-arm cost claim."
        ),
        does_not_license=(
            "You may not quote a cost per case, a cost ratio between arms, or a projected "
            "production cost from this run."
        ),
    ),
    "oracle_integrity": Criterion(
        id="oracle_integrity",
        severity=BLOCKING,
        statement=(
            "A PAID oracle was validated before its scores were trusted: canaries passed "
            "and repeatability was measured."
        ),
        citation=(
            "An LLM judge is an instrument, and an uncalibrated instrument produces "
            "numbers, not measurements. Canary + repeatability is the minimum this "
            "harness already implements (live_rag_judge preflight)."
        ),
        does_not_license=(
            "You may not treat the scores as a measurement of quality. They are one "
            "unvalidated model's opinion, of unknown stability."
        ),
    ),
    "reproducibility": Criterion(
        id="reproducibility",
        severity=QUALIFYING,
        statement=(
            "The run identifies the code that produced it, that code was not dirty, and "
            "the sampling knobs that change the numbers (seed, repeats) were recorded."
        ),
        citation=(
            "A result nobody can reproduce is an anecdote. RuntimeIdentity already "
            "captures commit_sha and a dirty flag; this check is that the flag was clean, "
            "not merely that the field exists. Seed and repeats are here because a rerun "
            "at the same commit with a different seed is not the same experiment."
        ),
        does_not_license=(
            "You may not cite this run as evidence for a commit. It measured a working "
            "tree nobody can reconstruct."
        ),
    ),
    "cost_source_honesty": Criterion(
        id="cost_source_honesty",
        severity=QUALIFYING,
        statement=(
            "At most "
            f"{MAX_CEILING_COST_SHARE:.0%} of rows were charged a fail-closed CEILING "
            "rather than a measured usage figure."
        ),
        citation=(
            "Ceilings are deliberately pessimistic (app/experiments/core/cost.py). They "
            "are the right charge and the wrong measurement: the arm that lost its usage "
            "data looks more expensive for a reason that is not price."
        ),
        does_not_license=(
            "You may not compare cost between arms. The ceiling share differs by arm and "
            "moves the ratio by more than most real effects."
        ),
    ),
    "frozen_contract_available": Criterion(
        id="frozen_contract_available",
        severity=QUALIFYING,
        statement=(
            "The frozen experiment document is reachable from the attempt directory and "
            "its hash matches the attempt's recorded freeze_hash."
        ),
        citation=(
            "The attempt directory records only a freeze_hash, not the contract itself. "
            "Without the document, a replayed review cannot know what the campaign "
            "INTENDED to compare -- only what it happened to produce."
        ),
        does_not_license=(
            "You may not claim this run executed the frozen design. Nothing here can "
            "check the matrix against the contract it was supposed to honour."
        ),
    ),
    "artifact_integrity": Criterion(
        id="artifact_integrity",
        severity=BLOCKING,
        statement="Every hashed artifact still matches the hash recorded at the time of the run.",
        citation=(
            "AttemptStore.hash_artifacts writes artifact_hashes.json at the end of the "
            "attempt. Re-verifying it is what makes a REPLAYED review a review of the "
            "original run rather than of whatever is in the directory now."
        ),
        does_not_license=(
            "You may not treat these artifacts as the run's output. At least one file "
            "changed after the attempt finished."
        ),
    ),
}
