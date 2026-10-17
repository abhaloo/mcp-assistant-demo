"""Frozen query-routing outcome matrix — replay any routing policy for $0.

This module answers TWO deliberately SEPARATE questions. They must never be
conflated — doing so would mean silently redefining what "correct" means,
which is the exact failure mode this harness exists to prevent.

**(a) Label agreement** (:func:`replay_label_decisions`,
:func:`label_agreement_score`) — does a candidate predicate's escalation
decision agree with a human's ``should_escalate`` tag on
``evals/sql/router_cases.jsonl``'s 50 cases? This is the IDENTICAL
computation ``scripts/eval/eval_router.py::score_router`` already performs
(compare hand label against ``predict(question)``; no model runs).
directly: ``grep -c gold_sql evals/sql/router_cases.jsonl`` = 0 — these 50
cases carry no gold SQL and were never executed against the SQL agent, so
there is nothing to compute a real outcome from. ``replay_label_decisions``
/ ``label_agreement_score`` exist as a reusable replay primitive with the
same math, NOT built from :class:`OutcomeCell` or :func:`score_policy` —
inventing an ``OutcomeCell.correct`` proxy for these cases (e.g. "correct" =
"matches the label") would silently redefine what an outcome cell means.
The regression lock (``test_replay_reproduces_score_router_decisions``)
asserts exact equality against ``scripts.eval.eval_router.score_router`` itself.

**(b) Real outcomes** (:class:`OutcomeCell`, :func:`score_policy`,
:func:`build_matrix_from_runs`) — did escalating actually help, for the
cases that were actually run through the SQL agent? ``evals/sql/cases.jsonl``
(44 cases) AND ``evals/sql/cases_holdout.jsonl`` (23 cases) both carry
``gold_sql`` and both have real per-(case, deployment) pass/fail + cost +
latency mined from ``scripts/eval/evaluate_sql_agent.py`` run files
(``evals/runs/sql/*.json``). Mine those run files ONCE
(:func:`build_matrix_from_runs`) into a frozen :class:`OutcomeCell` list,
then replay any routing rule over it (:func:`score_policy`) with zero LLM
calls and zero DB access.

**These two populations are disjoint and must stay reported separately.**
``evals/sql/router_cases.jsonl``'s 50 ids and the 67 SQL-eval case ids
(``cases.jsonl`` + ``cases_holdout.jsonl``) have ZERO id overlap (see
``test_router_case_ids_have_zero_overlap_with_sql_eval_case_ids``) —
question TEXT overlaps on 12 of the 50 (same wording, different id), which
does not change the conclusion: there is no gold to score a real outcome
against for those 50 ids at all. Any report built on this module must
present (a)'s label-agreement numbers and (b)'s outcome-based numbers as two
separate sections, never averaged or substituted for each other.

``OutcomeCell.deployment`` is a LOGICAL role — ``"default"`` or
``"escalated"`` — not the literal Azure/OpenAI deployment name. The literal
escalation deployment has changed across the run-file history (e.g.
``gpt-4.1`` in ``b2-grounding-*``) while the routing DECISION it represents
has not; pinning cells to a role keeps the matrix meaningful across that
drift. A cell's role is read from each run file's own ``escalated_cases``
list — the same signal ``eval_router.py::_pass_rates_for_deployment`` already
mines — never guessed from the raw ``chat_deployment`` string.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DEPLOYMENT = "default"
ESCALATED_DEPLOYMENT = "escalated"

# A case "passed" a deployment if at least half its repeats matched gold.
# Matches eval_router.py::_needs_escalation_from_rates' default threshold —
# kept identical so a cell's "correct" means the same thing that function's
# "passes on mini" / "passes on escalation" already means for this codebase.
_PASS_THRESHOLD = 0.5

# Same public list pricing as scripts/eval/evaluate_sql_agent.py (gpt-4o-mini,
# USD per 1M tokens). No shared constants module exists yet for this; keep
# these two files' pricing in sync by hand if the vendor's list price moves.
_USD_PER_1M_PROMPT = 0.15
_USD_PER_1M_CACHED_PROMPT = 0.075
_USD_PER_1M_COMPLETION = 0.60


@dataclass(frozen=True)
class OutcomeCell:
    """One (case, deployment) frozen outcome: correct, cost, latency."""

    case_id: str
    deployment: str
    correct: bool
    cost_usd: float
    latency_ms: float


def _run_cost_usd(run: dict) -> float:
    """Price one replayed run, discounting cached prompt tokens.

    Kept apart from estimate_cost_usd deliberately: prices.yaml carries no cached
    rate, so the shared pricer would charge cache hits at the full prompt rate.
    """
    tp = run.get("tokens_prompt", 0)
    tc = run.get("tokens_completion", 0)
    tcache = run.get("tokens_cached", 0)
    return (
        (tp - tcache) / 1e6 * _USD_PER_1M_PROMPT
        + tcache / 1e6 * _USD_PER_1M_CACHED_PROMPT
        + tc / 1e6 * _USD_PER_1M_COMPLETION
    )


def cells_from_run_file(path: str | Path, *, want_escalated: bool) -> list[OutcomeCell]:
    """Mine one evaluate_sql_agent.py run file for cells of ONE logical role.

    A run file mixes both roles (the router decides per-case at run time), so
    this only returns the subset whose actual escalation status (per the
    file's own ``escalated_cases`` list) matches ``want_escalated`` — the same
    partition ``eval_router.py::_pass_rates_for_deployment`` already applies.
    Cases with ``evaluated_runs <= 0`` (didn't run) are skipped, not scored.

    Rows with ``infra_error`` set (rate-limit/timeout) are excluded from the
    ``runs[]`` used for cost/latency — the exact same exclusion
    ``evaluate_sql_agent.py::aggregate`` already applies before computing
    ``pass_rate`` (which is what ``correct`` reads). Without this, a case
    with a mix of one real run and several timed-out retries would average
    its cost/latency across BOTH populations while ``correct`` reflects only
    the real run — a large, silent inflation (confirmed against a real
    fixture: one cell's latency came out 19x too high this way before this
    filter was added).

    A case declared with ``evaluated_runs > 0`` but with zero matching
    non-infra rows in ``runs[]`` is schema drift, not an empty result — raises
    loud rather than silently skipping (mirrors ``run_empirical_audit``'s
    fail-loud stance on a malformed run file).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    esc_set = set(data.get("escalated_cases") or [])
    role = ESCALATED_DEPLOYMENT if want_escalated else DEFAULT_DEPLOYMENT

    by_case: dict[str, list[dict]] = {}
    for r in data.get("runs", []):
        if r.get("infra_error"):
            continue
        by_case.setdefault(r["case_id"], []).append(r)

    cells: list[OutcomeCell] = []
    for case in data.get("cases", []):
        cid = case["case_id"]
        if case.get("evaluated_runs", 0) <= 0:
            continue
        ran_escalated = cid in esc_set
        if ran_escalated != want_escalated:
            continue
        runs = by_case.get(cid, [])
        if not runs:
            raise ValueError(
                f"{path}: case {cid!r} declares evaluated_runs={case['evaluated_runs']} "
                "but has zero matching non-infra-error rows in runs[] — schema drift, "
                "not an empty result."
            )
        cells.append(
            OutcomeCell(
                case_id=cid,
                deployment=role,
                correct=case["pass_rate"] >= _PASS_THRESHOLD,
                cost_usd=statistics.mean(_run_cost_usd(r) for r in runs),
                # Bare subscript deliberately, not .get(): evaluate_sql_agent.py's own
                # aggregate() treats latency_total_ms as required the same way
                # (`latencies = sorted(r["latency_total_ms"] for r in eval_runs)`) — a row
                # missing it is a data-quality bug worth crashing on, unlike tokens_* which
                # legitimately varies by cache/endpoint and defaults to 0 upstream too.
                latency_ms=statistics.mean(r["latency_total_ms"] for r in runs),
            )
        )
    return cells


def build_matrix_from_runs(
    mini_paths: Iterable[str | Path], escalated_paths: Iterable[str | Path]
) -> list[OutcomeCell]:
    """Merge cells from multiple run files into one frozen matrix.

    ``mini_paths`` contribute DEFAULT_DEPLOYMENT cells, ``escalated_paths``
    contribute ESCALATED_DEPLOYMENT cells — mirrors eval_router.py's
    ``_MINI_RUNS`` / ``_ESC_RUNS`` split. If two files define the same
    (case_id, role) cell, the later one wins (dev and holdout files are
    disjoint case-id sets by construction, so this should not occur in
    practice; last-write-wins is a deliberate, simple tiebreak rather than an
    error, since re-running the same case at the same role is a legitimate
    re-measurement).

    Raises if a non-empty ``mini_paths``/``escalated_paths`` list yields ZERO
    cells combined for its role — e.g. a run file's ``cases``/
    ``escalated_cases`` key renamed or typo'd would otherwise silently
    reclassify every case (or come back empty) with no signal at all,
    matching ``eval_router.py::run_empirical_audit``'s
    ``if not mini or not esc: raise SystemExit`` guard on exactly this
    condition.
    """
    mini_paths = list(mini_paths)
    escalated_paths = list(escalated_paths)
    merged: dict[tuple[str, str], OutcomeCell] = {}

    mini_total = 0
    for p in mini_paths:
        cells = cells_from_run_file(Path(p), want_escalated=False)
        mini_total += len(cells)
        for cell in cells:
            merged[(cell.case_id, cell.deployment)] = cell
    if mini_paths and mini_total == 0:
        raise ValueError(
            f"build_matrix_from_runs: {len(mini_paths)} mini_paths {mini_paths} yielded ZERO "
            "default-deployment cells combined — likely schema drift (a run file's 'cases' or "
            "'escalated_cases' key renamed/typo'd, or every case genuinely escalated everywhere)."
        )

    esc_total = 0
    for p in escalated_paths:
        cells = cells_from_run_file(Path(p), want_escalated=True)
        esc_total += len(cells)
        for cell in cells:
            merged[(cell.case_id, cell.deployment)] = cell
    if escalated_paths and esc_total == 0:
        raise ValueError(
            f"build_matrix_from_runs: {len(escalated_paths)} escalated_paths {escalated_paths} "
            "yielded ZERO escalated-deployment cells combined — likely schema drift."
        )

    return list(merged.values())


def replay_label_decisions(cases: list[dict], predict: Callable[[str], bool]) -> list[dict]:
    """Per-case escalation decision replay against a HUMAN LABEL, not an outcome.

    Deliberately NOT built from :class:`OutcomeCell` / :func:`score_policy`:
    ``router_cases.jsonl`` carries no ``gold_sql`` and was never run through
    the SQL agent (see module docstring), so there is no real outcome to
    score — only whether a candidate predicate's decision agrees with the
    human's ``should_escalate`` tag. ``cases`` must have ``id``,
    ``question``, ``should_escalate`` (``router_cases.jsonl`` schema).
    """
    decisions: list[dict] = []
    for case in cases:
        should = bool(case["should_escalate"])
        pred = bool(predict(case["question"]))
        decisions.append(
            {
                "case_id": case["id"],
                "should_escalate": should,
                "predicted_escalate": pred,
                "agrees": should == pred,
            }
        )
    return decisions


def label_agreement_score(cases: list[dict], predict: Callable[[str], bool]) -> dict:
    """Recall/precision of a predicate's decisions against human labels.

    This is the SAME computation as ``scripts/eval/eval_router.py::score_router``
    (compare ``predict(question)`` against ``should_escalate``; TP/FP/FN/TN
    over the decision, not an outcome) — reimplemented here, deliberately
    minimal, so a ``routing_matrix`` consumer can replay a candidate policy
    over the label set without reaching into the eval_router script. The
    regression lock in ``tests/experiments/test_routing_matrix.py`` asserts
    this reproduces ``score_router``'s tp/fp/fn/tn and recall/precision
    exactly, for both the real regex and an arbitrary stub predicate.
    """
    decisions = replay_label_decisions(cases, predict)
    tp = sum(1 for d in decisions if d["should_escalate"] and d["predicted_escalate"])
    fn = sum(1 for d in decisions if d["should_escalate"] and not d["predicted_escalate"])
    fp = sum(1 for d in decisions if not d["should_escalate"] and d["predicted_escalate"])
    tn = sum(1 for d in decisions if not d["should_escalate"] and not d["predicted_escalate"])
    n_pos = tp + fn
    n_esc = tp + fp
    return {
        "n": len(decisions),
        "n_pos": n_pos,
        "n_neg": tn + fp,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "recall": round(tp / n_pos, 4) if n_pos else None,
        "precision": round(tp / n_esc, 4) if n_esc else None,
        "decisions": decisions,
    }


def coverage_report(
    matrix: list[OutcomeCell],
    cases: list[dict],
    *,
    deployments: tuple[str, str] = (DEFAULT_DEPLOYMENT, ESCALATED_DEPLOYMENT),
) -> dict:
    """Which (case, deployment) cells a target case list actually has evidence
    for, vs. which are missing — explicit, never a silent cap on coverage."""
    have = {(c.case_id, c.deployment) for c in matrix}
    covered: list[dict] = []
    missing: list[dict] = []
    for case in cases:
        cid = case["id"]
        for dep in deployments:
            row = {"case_id": cid, "deployment": dep}
            (covered if (cid, dep) in have else missing).append(row)
    return {
        "n_total": len(covered) + len(missing),
        "n_covered": len(covered),
        "n_missing": len(missing),
        "covered": covered,
        "missing": missing,
    }


def _score_raw(
    lookup: dict[tuple[str, str], OutcomeCell],
    predict: Callable[[str], bool],
    cases: list[dict],
    *,
    default: str,
    escalated: str,
) -> dict[str, Any]:
    per_case: list[dict] = []
    missing: list[dict] = []
    for case in cases:
        cid = case["id"]
        chosen = escalated if predict(case["question"]) else default
        cell = lookup.get((cid, chosen))
        if cell is None:
            missing.append({"case_id": cid, "deployment": chosen})
            continue
        per_case.append(
            {
                "case_id": cid,
                "deployment": chosen,
                "correct": cell.correct,
                "cost_usd": cell.cost_usd,
                "latency_ms": cell.latency_ms,
            }
        )

    n_scored = len(per_case)
    n_escalated = sum(1 for pc in per_case if pc["deployment"] == escalated)
    return {
        "n_scored": n_scored,
        "n_missing": len(missing),
        "missing_cells": missing,
        "accuracy": round(sum(pc["correct"] for pc in per_case) / n_scored, 4)
        if n_scored
        else None,
        "cost_usd": round(sum(pc["cost_usd"] for pc in per_case), 6),
        "escalation_rate": round(n_escalated / n_scored, 4) if n_scored else None,
        "per_case": per_case,
    }


def _cases_with_both_cells(
    lookup: dict[tuple[str, str], OutcomeCell],
    cases: list[dict],
    *,
    default: str,
    escalated: str,
) -> list[dict]:
    """Cases where BOTH deployment cells exist — the only population on
    which a policy and a trivial baseline can be compared fairly. A case
    missing either cell would let one side of the comparison silently draw
    from a different (usually smaller, systematically easier-or-harder)
    subset than the other."""
    out = []
    for case in cases:
        cid = case["id"]
        if (cid, default) in lookup and (cid, escalated) in lookup:
            out.append(case)
    return out


def _delta(
    policy_common: dict[str, Any], baseline_common: dict[str, Any], n_common: int
) -> dict[str, Any]:
    """Policy vs. one trivial baseline, BOTH already scored on the SAME
    common-coverage population (see :func:`_cases_with_both_cells`) — never a
    policy's full-coverage accuracy diffed against a baseline's own,
    differently-sized coverage.

    Cost fields are ``None`` (not ``0``) when ``n_common == 0``: a bare 0
    would read as "measured, and it's a tie" when it actually means "no
    evidence exists to compare on at all" — the same None-on-no-evidence
    discipline ``accuracy_delta`` already applies.
    """
    acc_delta = (
        round(policy_common["accuracy"] - baseline_common["accuracy"], 4)
        if policy_common["accuracy"] is not None and baseline_common["accuracy"] is not None
        else None
    )
    return {
        "n_common": n_common,
        "policy_accuracy": policy_common["accuracy"],
        "baseline_accuracy": baseline_common["accuracy"],
        "baseline_cost_usd": baseline_common["cost_usd"] if n_common else None,
        "accuracy_delta": acc_delta,
        "cost_usd_delta": (
            round(policy_common["cost_usd"] - baseline_common["cost_usd"], 6) if n_common else None
        ),
    }


def _compute_regret(
    lookup: dict[tuple[str, str], OutcomeCell],
    cases: list[dict],
    predict: Callable[[str], bool],
    *,
    default: str,
    escalated: str,
) -> dict[str, Any]:
    """Cases where escalating bought nothing, or not escalating cost a win.

    Judged only where BOTH deployment cells exist for a case — regret about a
    counterfactual outcome we have no evidence for would be fabrication, not
    replay. Cases missing either cell are named in ``unjudgeable``, never
    silently absent: an empty ``escalated_no_gain``/``should_have_escalated``
    must not be misread as "no waste anywhere" when it may mean "could only
    judge n_judgeable of len(cases)" — mirrors ``eval_router.py``'s own
    ``audit_gaps``/``no_mini_evidence`` + ``coverage_note`` convention for the
    identical situation.
    """
    escalated_no_gain: list[str] = []
    should_have_escalated: list[str] = []
    unjudgeable: list[str] = []
    for case in cases:
        cid = case["id"]
        chose_escalated = predict(case["question"])
        chosen_dep = escalated if chose_escalated else default
        other_dep = default if chose_escalated else escalated
        chosen_cell = lookup.get((cid, chosen_dep))
        other_cell = lookup.get((cid, other_dep))
        if chosen_cell is None or other_cell is None:
            unjudgeable.append(cid)
            continue
        if chose_escalated:
            if other_cell.correct == chosen_cell.correct:
                escalated_no_gain.append(cid)
        elif other_cell.correct and not chosen_cell.correct:
            should_have_escalated.append(cid)
    return {
        "escalated_no_gain": escalated_no_gain,
        "should_have_escalated": should_have_escalated,
        "n_judgeable": len(cases) - len(unjudgeable),
        "unjudgeable": unjudgeable,
    }


def score_policy(
    matrix: list[OutcomeCell],
    predict: Callable[[str], bool],
    cases: list[dict],
    *,
    default: str = DEFAULT_DEPLOYMENT,
    escalated: str = ESCALATED_DEPLOYMENT,
) -> dict:
    """Replay a routing rule over frozen outcomes. No LLM calls, no DB.

    ``predict(question) -> bool`` decides escalate/default per case; the
    matching (case_id, deployment) cell is looked up in ``matrix``. A case
    with no matching cell is counted in ``missing_cells`` and excluded from
    every aggregate (never silently scored as a pass or a fail).

    ``cost_usd`` (like ``accuracy``) is computed over the policy's own
    natural coverage (``n_scored`` cases) — it is NOT directly comparable to
    another policy's ``cost_usd`` unless both share the same ``n_scored``;
    use ``vs_always_*`` (common-coverage) for a fair comparison instead.

    Reports the policy against both trivial baselines — always-default
    (cheapest) and always-escalated (most accurate any policy could target) —
    so a caller can check the policy actually earns its keep: it should not
    lose accuracy to always-escalated, and it should not lose cost to
    always-default. Each ``vs_always_*`` delta is computed on the subset of
    ``cases`` where BOTH deployment cells exist (``n_common``), never the
    policy's full coverage diffed against a differently-sized baseline
    coverage — see :func:`_delta`.
    """
    lookup = {(c.case_id, c.deployment): c for c in matrix}
    policy = _score_raw(lookup, predict, cases, default=default, escalated=escalated)
    regret = _compute_regret(lookup, cases, predict, default=default, escalated=escalated)

    common_cases = _cases_with_both_cells(lookup, cases, default=default, escalated=escalated)
    n_common = len(common_cases)
    policy_common = _score_raw(lookup, predict, common_cases, default=default, escalated=escalated)
    always_default_common = _score_raw(
        lookup, lambda _q: False, common_cases, default=default, escalated=escalated
    )
    always_escalated_common = _score_raw(
        lookup, lambda _q: True, common_cases, default=default, escalated=escalated
    )

    return {
        "n_cases": len(cases),
        "n_scored": policy["n_scored"],
        "n_missing": policy["n_missing"],
        "missing_cells": policy["missing_cells"],
        "accuracy": policy["accuracy"],
        "cost_usd": policy["cost_usd"],
        "escalation_rate": policy["escalation_rate"],
        "per_case": policy["per_case"],
        "vs_always_default": _delta(policy_common, always_default_common, n_common),
        "vs_always_escalated": _delta(policy_common, always_escalated_common, n_common),
        "regret": regret,
    }
