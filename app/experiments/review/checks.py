"""The deterministic campaign review: does this run's answer mean anything?

R1 of docs/superpowers/specs/2026-08-02-campaign-console-and-review-design.md.

NOT THE SAME QUESTION AS THE GATE
=================================
The campaign's own verdict answers "did the candidate beat the baseline" -- PASS /
FAIL / HOLD. This answers a different and prior question: **was the experiment valid
enough that its answer means anything at all** -- SOUND / QUALIFIED / UNSOUND.

The two vocabularies are deliberately disjoint. A PASS campaign with an UNSOUND review
is the exact situation this stage exists to surface, and if the words could be confused
the stage would be pointless.

THE VERDICT NEVER DEPENDS ON A MODEL
====================================
Everything here is pure, free, and offline. The narrator (R3) writes prose about these
findings and cannot change them -- the verdict is stamped from this module before it
runs. That ordering is the design's whole defence against a reviewer that says what you
want to hear.

A CHECK THAT COULD NOT RUN IS NOT A PASS
========================================
Every check returns ``pass``, ``fail``, ``not_applicable`` or ``not_evaluable``, and
none of the last three is ever silently folded into the first.

The final two are a distinction the review CANNOT do without, and collapsing them was
a real bug here:

* ``not_applicable`` -- the criterion does not BIND on this run's design. A single-arm
  run is not an unbalanced comparison, it is not a comparison; SQL's oracle executes the
  gold query and routing's asks whether the provider accepted, so neither has anything
  to calibrate. Demanding canaries of them would be theatre, and theatre in a review is
  worse than an honest gap. This is not a defect and must not cost the run anything.
* ``not_evaluable`` -- the criterion binds, and the evidence needed to judge it was
  missing or unreadable. That IS a defect in the record. A blocking criterion in this
  state caps the verdict at QUALIFIED: a verdict that would have rested on evidence
  nobody gathered is not a pass.

One status for both meant either that every SQL and routing campaign was capped at
QUALIFIED forever by its own free oracle -- a downgrade that never varies and so
carries no information -- or that a directory missing its blocking evidence reviewed
as SOUND. ``arm_balance`` shows why the line has to be drawn from EVIDENCE rather than
by convention: a single-arm run whose frozen contract declared two arms did not choose
a single-arm design, it LOST an arm, and that is a hard failure rather than either
kind of skip.

PURE FUNCTION OVER A DIRECTORY
==============================
``review_attempt`` reads a path and returns a value. Every ``Campaign``-driven campaign
-- RAG, SQL and routing alike -- reviews itself in-run through this same function; the
purity is what ALSO makes replay free, so ``review_campaign.py <attempt-dir>`` can run
the identical code over any historical attempt directory.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.eval.run_stats import mcnemar_exact_p
from app.experiments.review.criteria import (
    BLOCKING,
    CRITERIA,
    CRITERIA_VERSION,
    FAILURE_BALANCE_ALPHA,
    MAX_CEILING_COST_SHARE,
    MIN_PAIRED_CASES,
    SPEND_RECONCILIATION_TOLERANCE_USD,
)

__all__ = [
    "Finding",
    "ReviewResult",
    "review_attempt",
]

PASS = "pass"
FAIL = "fail"
#: The criterion does not bind on this run's design. Not a defect.
NOT_APPLICABLE = "not_applicable"
#: The criterion binds; the evidence to judge it was missing or unreadable.
NOT_EVALUABLE = "not_evaluable"

#: Statuses that are not a verified pass, in report order.
_NON_PASS = (FAIL, NOT_APPLICABLE, NOT_EVALUABLE)

SOUND = "SOUND"
QUALIFIED = "QUALIFIED"
UNSOUND = "UNSOUND"

TRIGGER_IN_CAMPAIGN = "in_campaign"
TRIGGER_REPLAY = "replay"

#: How the report stamps its verdict. Named because ``narrator.narrative_section`` has
#: to neutralise exactly this token in prose -- a second one in the same document is a
#: second answer to the question the document exists to answer.
VERDICT_STAMP = "**Verdict:"


@dataclass(frozen=True)
class Finding:
    id: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def criterion(self) -> Any:
        return CRITERIA[self.id]

    @property
    def severity(self) -> str:
        """Read from the criterion, never passed in.

        Severity is a property of the STANDARD, not of the branch a check happened to
        take. Restating it at each return let two branches of one check disagree about
        how much its own failure weighs, and nothing would have caught it -- so it lives
        in ``criteria.py`` with the thresholds, and there is no second place to write it.
        """
        return str(self.criterion.severity)

    def to_dict(self) -> dict[str, Any]:
        criterion = self.criterion
        document = {
            "id": self.id,
            "status": self.status,
            "severity": self.severity,
            "statement": criterion.statement,
            "citation": criterion.citation,
            "evidence": self.evidence,
        }
        # The sentence a reader actually needs, and only when it applies. Printing
        # "you may not claim X" beside a PASSING check trains people to skip the field.
        if self.status == FAIL:
            document["what_this_does_not_license"] = criterion.does_not_license
        return document


@dataclass(frozen=True)
class ReviewResult:
    verdict: str
    trigger: str
    attempt_id: str
    criteria_version: str
    findings: list[Finding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "verdict": self.verdict,
            # Which path produced this. Without it, a RAG run's silence reads as "the
            # review passed" when in fact the review never ran.
            "review_trigger": self.trigger,
            "attempt_id": self.attempt_id,
            "criteria_version": self.criteria_version,
            "counts": {
                status: sum(1 for finding in self.findings if finding.status == status)
                for status in (PASS, *_NON_PASS)
            },
            "findings": [finding.to_dict() for finding in self.findings],
        }


# --------------------------------------------------------------------- artifact IO


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return None
        if isinstance(row, dict):
            rows.append(row)
    return rows


@dataclass(frozen=True)
class _Attempt:
    """Everything the checks read, loaded once. ``None`` means "this artifact was not
    readable", which is what turns a check into ``not_applicable`` rather than a pass."""

    directory: Path
    attempt: dict[str, Any] | None
    answers: list[dict[str, Any]] | None
    verdict: dict[str, Any] | None
    oracle_validation: dict[str, Any] | None
    artifact_hashes: dict[str, str] | None
    #: The frozen contract, resolved ONCE by hash. ``None`` when the attempt records no
    #: freeze_hash, or when no document under the run group matches it -- so two checks
    #: (availability, and the seed/repeats half of reproducibility) share one answer
    #: rather than each re-globbing and each deciding for itself what "found" means.
    frozen_name: str | None
    frozen_document: dict[str, Any] | None
    frozen_candidates: list[str]

    @property
    def attempt_id(self) -> str:
        if isinstance(self.attempt, dict):
            return str(self.attempt.get("attempt_id") or self.directory.name)
        return self.directory.name

    @property
    def frozen_policy(self) -> dict[str, Any] | None:
        policy = (self.frozen_document or {}).get("policy")
        return policy if isinstance(policy, dict) else None


def _resolve_frozen(
    directory: Path, expected: Any
) -> tuple[str | None, dict[str, Any] | None, list[str]]:
    """Find the frozen document whose hash matches, under the attempt's run group.

    Matching is by HASH, never by filename: a document that merely sits in the right
    place proves nothing about what this attempt was run against.
    """
    if not expected:
        return None, None, []
    # attempts/<attempt-id>/ sits under the run group that holds the frozen document.
    run_group = directory.parent.parent
    candidates = sorted(run_group.glob("frozen-*.json")) if run_group.is_dir() else []
    for candidate in candidates:
        try:
            digest = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest == str(expected):
            document = _read_json(candidate)
            return (
                candidate.name,
                (document if isinstance(document, dict) else None),
                [path.name for path in candidates],
            )
    return None, None, [path.name for path in candidates]


def _load(directory: Path) -> _Attempt:
    # RAG names it judge_validation.json; SQL and routing name it oracle_validation.json.
    # Same role, two names, and the review reads whichever is present rather than making
    # the adapters agree -- renaming a shipped artifact would break every existing
    # attempt directory this is supposed to be able to replay.
    oracle = _read_json(directory / "judge_validation.json")
    if oracle is None:
        oracle = _read_json(directory / "oracle_validation.json")
    attempt = _read_json(directory / "attempt.json")
    freeze_hash = attempt.get("freeze_hash") if isinstance(attempt, dict) else None
    frozen_name, frozen_document, frozen_candidates = _resolve_frozen(directory, freeze_hash)
    return _Attempt(
        directory=directory,
        attempt=attempt,
        answers=_read_jsonl(directory / "answers.jsonl"),
        verdict=_read_json(directory / "verdict.json"),
        oracle_validation=oracle if isinstance(oracle, dict) else None,
        artifact_hashes=_read_json(directory / "artifact_hashes.json"),
        frozen_name=frozen_name,
        frozen_document=frozen_document,
        frozen_candidates=frozen_candidates,
    )


def _cell(row: dict[str, Any]) -> tuple[str, str, int]:
    return (str(row.get("case_id")), str(row.get("arm")), int(row.get("repeat", -1)))


def _arms(rows: Iterable[dict[str, Any]]) -> dict[str, set[tuple[str, int]]]:
    by_arm: dict[str, set[tuple[str, int]]] = {}
    for row in rows:
        by_arm.setdefault(str(row.get("arm")), set()).add(
            (str(row.get("case_id")), int(row.get("repeat", -1)))
        )
    return by_arm


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ------------------------------------------------------------------------- checks


def _check_matrix_integrity(data: _Attempt) -> Finding:
    if not data.answers:
        return Finding("matrix_integrity", NOT_EVALUABLE, {"reason": "no answers.jsonl"})
    cells = [_cell(row) for row in data.answers]
    duplicates = sorted({cell for cell in cells if cells.count(cell) > 1})
    status = FAIL if duplicates else PASS
    return Finding(
        "matrix_integrity",
        status,
        {
            "rows": len(cells),
            "distinct_cells": len(set(cells)),
            "duplicated_cells": [list(cell) for cell in duplicates[:10]],
        },
    )


def _declared_arms(policy: dict[str, Any] | None) -> set[str] | None:
    """Arm names from frozen policy.

    Frozen policies declare arms under ``arms`` or ``answer_arms`` for historical
    RAG runs.
    """
    if policy is None:
        return None
    # Prefer arms (SQL + future unified); fall back to answer_arms (legacy RAG).
    for key in ("arms", "answer_arms"):
        declared = policy.get(key)
        if isinstance(declared, (list, tuple)) and declared:
            names: set[str] = set()
            for arm in declared:
                names.add(str(arm.get("name", arm)) if isinstance(arm, dict) else str(arm))
            return names
    return None


def _check_arm_balance(data: _Attempt) -> Finding:
    if not data.answers:
        return Finding("arm_balance", NOT_EVALUABLE, {"reason": "no answers.jsonl"})
    by_arm = _arms(data.answers)
    if len(by_arm) < 2:
        # A single-arm run is not unbalanced -- it is not a comparison at all, and every
        # paired check below says so on its own terms. But "not a comparison" is only
        # benign when it was the DESIGN: ask the frozen contract. A run whose contract
        # declared two arms and produced one did not choose a single-arm design, it lost
        # an arm, and reporting that as a benign skip would hide the most consequential
        # thing that happened. RoutingTask is single-arm by construction (routing_task.py
        # ARM_NAME) and lands in the benign branch honestly.
        declared = _declared_arms(data.frozen_policy)
        if declared is not None and len(declared) >= 2:
            return Finding(
                "arm_balance",
                FAIL,
                {
                    "arms": sorted(by_arm),
                    "declared_arms": sorted(declared),
                    "reason": "the frozen contract declared a comparison this run did not run",
                },
            )
        return Finding(
            "arm_balance",
            NOT_APPLICABLE,
            {
                "arms": sorted(by_arm),
                "declared_arms": sorted(declared) if declared is not None else None,
                "reason": "single-arm design; not a comparison",
            },
        )
    shared: set[tuple[str, int]] = set.intersection(*by_arm.values())
    unpaired = {arm: len(cells - shared) for arm, cells in by_arm.items()}
    status = FAIL if any(unpaired.values()) else PASS
    return Finding(
        "arm_balance",
        status,
        {
            "arms": sorted(by_arm),
            "paired_cells": len(shared),
            "unpaired_cells_by_arm": unpaired,
        },
    )


def _check_statistical_power(data: _Attempt) -> Finding:
    if not data.answers:
        return Finding("statistical_power", NOT_EVALUABLE, {"reason": "no answers.jsonl"})
    by_arm = _arms(data.answers)
    if len(by_arm) < 2:
        return Finding("statistical_power", NOT_APPLICABLE, {"reason": "single arm"})
    # Distinct CASES, not rows: three repeats of one case are three measurements of the
    # same case, and counting them as three would overstate the sample by the repeat
    # factor -- the exact mistake this check exists to prevent someone else making.
    paired_cases = len({case for cells in by_arm.values() for case, _repeat in cells})
    status = PASS if paired_cases >= MIN_PAIRED_CASES else FAIL
    return Finding(
        "statistical_power",
        status,
        {"paired_cases": paired_cases, "minimum": MIN_PAIRED_CASES},
    )


def _check_failure_balance(data: _Attempt) -> Finding:
    if not data.answers:
        return Finding("failure_balance", NOT_EVALUABLE, {"reason": "no answers.jsonl"})
    failures_by_arm: dict[str, dict[str, bool]] = {}
    for row in data.answers:
        arm = str(row.get("arm"))
        case = str(row.get("case_id"))
        failed = bool(row.get("operational_failure"))
        # A case counts as failed for an arm if ANY of its repeats failed: the campaign
        # cannot use a case whose repeats disagree, so partial failure is failure.
        failures_by_arm.setdefault(arm, {})
        failures_by_arm[arm][case] = failures_by_arm[arm].get(case, False) or failed

    if len(failures_by_arm) != 2:
        return Finding(
            "failure_balance",
            NOT_APPLICABLE,
            {"reason": "test is defined for exactly two arms", "arms": sorted(failures_by_arm)},
        )
    left, right = (failures_by_arm[arm] for arm in sorted(failures_by_arm))
    shared_cases = sorted(set(left) & set(right))
    # Discordant pairs: cases where exactly one arm failed. McNemar looks only at these
    # -- cases that both arms passed or both failed carry no information about which arm
    # is worse.
    b = sum(1 for case in shared_cases if left[case] and not right[case])
    c = sum(1 for case in shared_cases if right[case] and not left[case])
    p_value = mcnemar_exact_p(b, c)
    status = FAIL if p_value < FAILURE_BALANCE_ALPHA else PASS
    return Finding(
        "failure_balance",
        status,
        {
            "arms": sorted(failures_by_arm),
            "discordant_pairs": {"only_first_failed": b, "only_second_failed": c},
            "p_value": p_value,
            "alpha": FAILURE_BALANCE_ALPHA,
        },
    )


def _check_spend_reconciliation(data: _Attempt) -> Finding:
    if not data.answers:
        return Finding("spend_reconciliation", NOT_EVALUABLE, {"reason": "no answers.jsonl"})
    row_total = sum(_float(row.get("cost_usd")) or 0.0 for row in data.answers)
    reported = None
    if isinstance(data.verdict, dict):
        for key in ("actual_spend_usd", "cost_usd_accounted", "spend_usd"):
            reported = _float(data.verdict.get(key))
            if reported is not None:
                break
    if reported is None:
        return Finding(
            "spend_reconciliation",
            NOT_EVALUABLE,
            {"reason": "verdict.json carries no spend figure", "row_cost_total_usd": row_total},
        )
    delta = abs(row_total - reported)
    status = PASS if delta <= SPEND_RECONCILIATION_TOLERANCE_USD else FAIL
    return Finding(
        "spend_reconciliation",
        status,
        {
            "row_cost_total_usd": row_total,
            "attempt_reported_usd": reported,
            "difference_usd": delta,
            "tolerance_usd": SPEND_RECONCILIATION_TOLERANCE_USD,
        },
    )


def _check_oracle_integrity(data: _Attempt) -> Finding:
    validation = data.oracle_validation
    if not isinstance(validation, dict):
        return Finding(
            "oracle_integrity",
            NOT_EVALUABLE,
            {"reason": "no oracle validation artifact"},
        )
    details = validation.get("details")
    cost = _float(details.get("cost_usd")) if isinstance(details, dict) else None
    was_paid = bool(cost)
    if not was_paid:
        # A free oracle has nothing to calibrate: SQL executes the gold query and
        # routing asks whether the provider accepted. Demanding canaries of them would
        # be theatre, and theatre in a review is worse than a gap.
        return Finding(
            "oracle_integrity",
            NOT_APPLICABLE,
            {"reason": "oracle reported no cost; nothing to calibrate"},
        )
    canaries = bool(validation.get("canaries_passed"))
    repeatability = bool(validation.get("repeatability_passed"))
    status = PASS if canaries and repeatability else FAIL
    return Finding(
        "oracle_integrity",
        status,
        {
            "oracle_cost_usd": cost,
            "canaries_passed": canaries,
            "repeatability_passed": repeatability,
        },
    )


def _check_reproducibility(data: _Attempt) -> Finding:
    runtime = (data.attempt or {}).get("runtime") if isinstance(data.attempt, dict) else None
    if not isinstance(runtime, dict):
        return Finding("reproducibility", NOT_EVALUABLE, {"reason": "no runtime in attempt.json"})
    commit_sha = runtime.get("commit_sha")
    dirty = runtime.get("dirty")
    # `dirty` absent is NOT clean. A runtime that did not record the flag cannot testify
    # that the tree was clean, and reading a missing field as False is how an unclean
    # run gets attested as reproducible.
    code_identified = bool(commit_sha) and dirty is False

    # The sampling knobs live in the frozen contract, not in `runtime`: a rerun at the
    # same commit with a different seed or repeat count is not the same experiment, so
    # "reproducible" has to mean both halves. Missing only because the contract could
    # not be resolved is reported as such rather than counted against the run --
    # frozen_contract_available already carries that failure, and charging one problem
    # to two criteria would overstate how much is wrong.
    policy = data.frozen_policy
    missing_knobs: list[str] = []
    knobs: dict[str, Any] = {}
    if policy is not None:
        for knob in ("bootstrap_seed", "repeats"):
            value = policy.get(knob)
            knobs[knob] = value
            if value is None:
                missing_knobs.append(knob)

    status = PASS if code_identified and not missing_knobs else FAIL
    return Finding(
        "reproducibility",
        status,
        {
            "commit_sha": commit_sha,
            "dirty": dirty,
            "sampling_knobs": knobs,
            "missing_sampling_knobs": missing_knobs,
            "sampling_knobs_checked": policy is not None,
        },
    )


def _intervals(node: Any, path: str = "") -> list[tuple[str, float, float]]:
    """Every ``{ci_lo, ci_hi}`` block under ``verdict.json`` details (depth varies by task)."""
    found: list[tuple[str, float, float]] = []
    if isinstance(node, dict):
        low = _float(node.get("ci_lo"))
        high = _float(node.get("ci_hi"))
        if low is not None and high is not None:
            found.append((path or "verdict", low, high))
        for key, value in node.items():
            found.extend(_intervals(value, f"{path}.{key}" if path else str(key)))
    return found


def _check_ci_excludes_zero(data: _Attempt) -> Finding:
    if not isinstance(data.verdict, dict):
        return Finding("ci_excludes_zero", NOT_EVALUABLE, {"reason": "no verdict.json"})
    intervals = _intervals(data.verdict.get("details"))
    if not intervals:
        # A single-arm run has no paired delta to put an interval around, so recording
        # none is correct. A run with two arms that recorded none is missing the
        # evidence for the only claim anyone reads off a comparison.
        is_comparison = bool(data.answers) and len(_arms(data.answers)) >= 2
        return Finding(
            "ci_excludes_zero",
            NOT_EVALUABLE if is_comparison else NOT_APPLICABLE,
            {
                "reason": "verdict.json records no paired confidence interval",
                "arms_observed": len(_arms(data.answers)) if data.answers else 0,
            },
        )
    straddling = [
        {"interval": name, "ci_lo": low, "ci_hi": high}
        for name, low, high in intervals
        if low <= 0.0 <= high
    ]
    status = FAIL if straddling else PASS
    return Finding(
        "ci_excludes_zero",
        status,
        {
            "intervals_checked": len(intervals),
            "intervals_containing_zero": straddling,
        },
    )


def _check_cost_source_honesty(data: _Attempt) -> Finding:
    if not data.answers:
        return Finding("cost_source_honesty", NOT_EVALUABLE, {"reason": "no answers.jsonl"})
    sources = [str(row.get("cost_source") or "") for row in data.answers]
    labelled = [source for source in sources if source]
    if not labelled:
        return Finding(
            "cost_source_honesty",
            NOT_EVALUABLE,
            {"reason": "rows carry no cost_source label", "rows": len(sources)},
        )
    ceilings = [source for source in labelled if source.startswith("ceiling")]
    share = len(ceilings) / len(labelled)
    status = PASS if share <= MAX_CEILING_COST_SHARE else FAIL
    by_reason: dict[str, int] = {}
    for source in ceilings:
        by_reason[source] = by_reason.get(source, 0) + 1
    return Finding(
        "cost_source_honesty",
        status,
        {
            "labelled_rows": len(labelled),
            "ceiling_rows": len(ceilings),
            "ceiling_share": share,
            "maximum_share": MAX_CEILING_COST_SHARE,
            "ceiling_reasons": by_reason,
        },
    )


def _check_frozen_contract_available(data: _Attempt) -> Finding:
    expected = (data.attempt or {}).get("freeze_hash") if isinstance(data.attempt, dict) else None
    if not expected:
        return Finding(
            "frozen_contract_available",
            NOT_EVALUABLE,
            {"reason": "attempt.json records no freeze_hash"},
        )
    if data.frozen_name is not None:
        return Finding(
            "frozen_contract_available",
            PASS,
            {"frozen_document": data.frozen_name, "freeze_hash": expected},
        )
    return Finding(
        "frozen_contract_available",
        FAIL,
        {
            "freeze_hash": expected,
            # The run group's NAME, never its path. Evidence dicts are interpolated into
            # the R3 narrator's prompt, and an absolute path carries the operator's OS
            # username and local directory layout to a provider -- environment data that
            # tells a reader of the report nothing they needed.
            "searched": data.directory.parent.parent.name,
            "candidates": data.frozen_candidates,
        },
    )


def _check_artifact_integrity(data: _Attempt) -> Finding:
    hashes = data.artifact_hashes
    if not isinstance(hashes, dict) or not hashes:
        return Finding(
            "artifact_integrity",
            NOT_EVALUABLE,
            {"reason": "no artifact_hashes.json"},
        )
    mismatched: list[str] = []
    missing: list[str] = []
    for name, expected in sorted(hashes.items()):
        path = data.directory / name
        try:
            actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            missing.append(name)
            continue
        if actual != str(expected):
            mismatched.append(name)
    status = FAIL if (mismatched or missing) else PASS
    return Finding(
        "artifact_integrity",
        status,
        {"hashed_files": len(hashes), "mismatched": mismatched, "missing": missing},
    )


_CHECKS = (
    _check_matrix_integrity,
    _check_arm_balance,
    _check_statistical_power,
    _check_failure_balance,
    _check_ci_excludes_zero,
    _check_spend_reconciliation,
    _check_oracle_integrity,
    _check_reproducibility,
    _check_cost_source_honesty,
    _check_frozen_contract_available,
    _check_artifact_integrity,
)


def _verdict_for(findings: list[Finding]) -> str:
    """Four rules, each statable in one sentence, all keyed off severity.

    An earlier version weighed coverage as a raw share of checks that ran
    (``applicable / len(findings) < 0.6``). That was wrong in kind: it counted an
    unevaluated BLOCKING criterion as coverage and nothing else, so a single-arm run --
    which ``RoutingTask`` produces BY CONSTRUCTION -- skipped the three checks that
    decide whether a comparison happened at all and still reviewed as SOUND. The share
    could not have caught it either, because the not-applicable outcomes are heavily
    correlated (six checks gate on one ``answers.jsonl`` load, four on "fewer than two
    arms"), so the ratio moves in lumps unrelated to how much the review learned.
    """
    if any(f.status == FAIL and f.severity == BLOCKING for f in findings):
        return UNSOUND
    if any(f.status == FAIL for f in findings):
        return QUALIFIED
    # Nothing was actually verified -- an unreadable or empty directory. QUALIFIED would
    # read as "mostly fine with caveats", which is a claim about a run nobody can see.
    # You cannot believe a result you were unable to look at.
    if not any(f.status == PASS for f in findings):
        return UNSOUND
    # A criterion the run's soundness DEPENDS on, whose evidence was missing, is not a
    # pass: it is the absence of the evidence the verdict would have rested on. Note
    # this reads NOT_EVALUABLE and NOT the by-design skips -- see the module docstring.
    if any(f.status == NOT_EVALUABLE and f.severity == BLOCKING for f in findings):
        return QUALIFIED
    return SOUND


def review_attempt(attempt_dir: Path, *, trigger: str = TRIGGER_REPLAY) -> ReviewResult:
    """Review one attempt directory. Pure: reads files, returns a value, spends nothing.

    Never raises for a bad directory. A review that crashed on a malformed attempt would
    be useless exactly where it is most needed, and -- wired in-run (R2) -- it must not
    be able to turn a completed campaign into a failed one.
    """
    data = _load(Path(attempt_dir))
    findings = [check(data) for check in _CHECKS]
    return ReviewResult(
        verdict=_verdict_for(findings),
        trigger=trigger,
        attempt_id=data.attempt_id,
        criteria_version=CRITERIA_VERSION,
        findings=findings,
    )


def render_markdown(result: ReviewResult, narrative: Any | None = None) -> str:
    """The human report. The verdict is written from ``result`` here, BEFORE any
    narrator runs (R3), so no prose stage can promote UNSOUND to SOUND.

    ``narrative`` is appended UNDERNEATH the findings, never above them. There is no
    argument by which prose reaches the verdict line."""
    counts = result.to_dict()["counts"]
    lines = [
        # ASCII only, deliberately. This string is printed to stdout by the replay CLI,
        # and a raw Windows console is often cp1252 -- the same codec that raises on the
        # dashboard's block glyphs. A review that crashes while reporting is worse than
        # a plain hyphen.
        f"# Campaign review - {result.attempt_id}",
        "",
        f"{VERDICT_STAMP} {result.verdict}**",
        "",
        f"- trigger: `{result.trigger}`",
        f"- criteria version: `{result.criteria_version}`",
        f"- checks: {counts[PASS]} passed, {counts[FAIL]} failed, "
        f"{counts[NOT_APPLICABLE]} did not apply, {counts[NOT_EVALUABLE]} not evaluable",
        "",
        "This verdict is about whether the experiment was VALID, not about whether the",
        "candidate won. The campaign's own gate answers that, in its own vocabulary.",
        "",
        "## Findings",
        "",
    ]
    for finding in result.findings:
        marker = {PASS: "PASS", FAIL: "FAIL", NOT_APPLICABLE: "n/a ", NOT_EVALUABLE: "????"}[
            finding.status
        ]
        lines.append(f"### `{marker}` {finding.id}")
        lines.append("")
        lines.append(finding.criterion.statement)
        lines.append("")
        lines.append(f"```json\n{json.dumps(finding.evidence, indent=2, sort_keys=True)}\n```")
        lines.append("")
        if finding.status == FAIL:
            lines.append(f"**This does not license:** {finding.criterion.does_not_license}")
            lines.append("")
        elif finding.status == NOT_APPLICABLE:
            lines.append("_Did not apply to this run's design. Not a defect, and not a pass._")
            lines.append("")
        elif finding.status == NOT_EVALUABLE:
            lines.append(
                "_Could NOT be checked -- the evidence was missing or unreadable. "
                "This is not a pass, and the gap is itself a finding._"
            )
            lines.append("")
    if narrative is not None:
        # Imported here, not at module scope: the narrator imports THIS module, and
        # more to the point the deterministic review has to keep working with no
        # narrator in the picture at all.
        from app.experiments.review.narrator import narrative_section

        lines.extend(narrative_section(narrative))
    return "\n".join(lines)
