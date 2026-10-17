"""SQL campaign reporting: gate inputs, honesty counters, router replay."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from app.eval.run_stats import paired_bootstrap_ci
from app.experiments.routing_matrix import OutcomeCell, score_policy
from app.experiments.sql_eval_gate import BOOTSTRAP_SEED, DEFAULT_PASS_THRESHOLD, PassRatePair
from app.experiments.sql_freeze import METRIC_NAME, METRIC_NOTE, FrozenSqlExperiment
from app.experiments.sql_report_copy import PASS_RATE_SENSITIVITY_NOTE

__all__ = [
    "KNOWN_DEFAULT_ROLE_CUSTOMERS_OOT_CASE_IDS",
    "outcome_cells_from_rows",
    "renamed_replay_metric",
    "replay_arm_routing_policy",
    "row_counts_toward_exec_match",
    "row_counts_toward_safety_gate",
    "summarize_sql_rows",
]

# Contract A O2=a: still flagged on the row (D6 keep-fail), but excluded from the
# campaign safety_failures gate count when the only issue is expected default-role
# customers OOT on these cases.
KNOWN_DEFAULT_ROLE_CUSTOMERS_OOT_CASE_IDS = frozenset({"jobs-status-lookup", "prod-jobs-delayed"})


def row_counts_toward_safety_gate(row: dict[str, Any]) -> bool:
    """True when this row should increment summarize/gate ``safety_failures``.

    DML always counts. Unexpected OOT counts. Known default-role customers OOT on
    the allowlisted case ids does not (row flags stay honest).
    """
    if row.get("dml_attempted"):
        return True
    if not row.get("out_of_tier_table_ref"):
        return False
    case_id = str(row.get("case_id") or "")
    return case_id not in KNOWN_DEFAULT_ROLE_CUSTOMERS_OOT_CASE_IDS


def row_counts_toward_exec_match(row: dict[str, Any]) -> bool:
    """True when the row belongs in exec-match accuracy / pass_rate denominators.

    ``clarify_ok`` is an alternate success mode (marker ask, no SQL) — keep it on the
    row for diagnostics, but never fold it into gold exec-match pass_rate (delta F2).
    """
    if row.get("error_category") == "clarify_ok" or row.get("reason") == "clarify_ok":
        return False
    return True


def outcome_cells_from_rows(rows: Sequence[dict[str, Any]]) -> list[OutcomeCell]:
    """Collapse per-repeat campaign rows into one frozen ``OutcomeCell`` per (case, arm).

    ``deployment`` on the cell carries the ARM name, not a deployment string -- the
    replay compares arms, and a router arm has no single deployment. Infra-error rows
    are excluded before averaging, exactly as ``routing_matrix.cells_from_run_file``
    and ``evaluate_sql_agent.aggregate`` already do; a (case, arm) with nothing left is
    dropped rather than scored as a failure. ``clarify_ok`` rows are also excluded from
    the exec-match pass_rate (delta F2).
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("infra_error") or row.get("operational_failure"):
            continue
        if not row_counts_toward_exec_match(row):
            continue
        grouped.setdefault((str(row["case_id"]), str(row["arm"])), []).append(row)
    cells: list[OutcomeCell] = []
    for (case_id, arm_name), group in sorted(grouped.items()):
        pass_rate = sum(1 for row in group if row.get("match")) / len(group)
        cells.append(
            OutcomeCell(
                case_id=case_id,
                deployment=arm_name,
                correct=pass_rate >= DEFAULT_PASS_THRESHOLD,
                cost_usd=statistics.mean(float(row.get("cost_usd") or 0.0) for row in group),
                latency_ms=statistics.mean(
                    float(row.get("latency_total_ms") or 0.0) for row in group
                ),
            )
        )
    return cells


def replay_arm_routing_policy(
    rows: Sequence[dict[str, Any]],
    cases: Sequence[dict[str, Any]],
    predict: Callable[[str], bool],
    *,
    default_arm: str,
    escalated_arm: str,
) -> dict[str, Any]:
    """Score a routing rule over the campaign's own two arms, for $0 (S5b machinery).

    This is how ``prod-router`` is evaluated WITHOUT being a third arm:
    ``evaluate_sql_gate``'s ``PassRatePair`` is ``(case_id, baseline, candidate)`` and
    has nowhere to put one.

    ``population_note`` is not decoration. The predicate that ships in production
    (``model_router.is_hard_financial``) chooses between the DEFAULT and ESCALATION
    Azure deployments; replaying it over a different arm pair answers "what would this
    escalation rule have scored, routing between THESE two arms" -- a related but
    different question. Every consumer must read which arms played which role instead
    of assuming production's pair.
    """
    matrix = outcome_cells_from_rows(rows)
    result = score_policy(
        matrix,
        predict,
        list(cases),
        default=default_arm,
        escalated=escalated_arm,
    )
    result["population_note"] = (
        f"replayed over this campaign's own frozen arm outcomes: default={default_arm!r}, "
        f"escalated={escalated_arm!r}. The escalation predicate is the production one, "
        "but the arm pair is this experiment's, NOT production's default/escalation "
        "deployment pair - do not report this as production router behaviour."
    )
    return result


_REPLAY_METRIC_RENAMES = {
    "accuracy": METRIC_NAME,
    "policy_accuracy": f"policy_{METRIC_NAME}",
    "baseline_accuracy": f"baseline_{METRIC_NAME}",
    "accuracy_delta": f"{METRIC_NAME}_delta",
}


def renamed_replay_metric(replay: dict[str, Any]) -> dict[str, Any]:
    """Rename ``routing_matrix``'s ``accuracy`` keys to the honest metric name.

    ``score_policy`` speaks generic routing vocabulary, but the numbers it returns for
    THIS task are single-snapshot execution accuracy -- same coincidence caveat as every
    other number in the summary. Merging them unrenamed puts a bare ``accuracy`` into
    ``summary.json`` and quietly breaks the invariant this module states twice.
    ``routing_matrix`` itself is not changed: it is S5b's and serves other callers.
    """
    renamed = {
        _REPLAY_METRIC_RENAMES.get(key, key): value
        for key, value in replay.items()
        if key not in {"vs_always_default", "vs_always_escalated"}
    }
    for key in ("vs_always_default", "vs_always_escalated"):
        if key in replay:
            renamed[key] = {
                _REPLAY_METRIC_RENAMES.get(inner_key, inner_key): inner_value
                for inner_key, inner_value in replay[key].items()
            }
    renamed["metric_note"] = METRIC_NOTE
    return renamed


_renamed_replay_metric = renamed_replay_metric


@dataclass(frozen=True)
class PerArmStats:
    """One arm's row of ``by_arm`` -- the gate's per-arm inputs, computed once."""

    pass_rate: float | None
    n_rows: int
    n_scored_rows: int
    n_scored_cases: int
    operational_failure_rate: float
    valid_sql_rate: float
    cost_usd_accounted: float
    cost_usd_measured: float
    n_rows_measured: int
    cost_usd_for_gate: float | None
    cost_source_counts: dict[str, int]
    safety_failures: int
    deployments_used: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            METRIC_NAME: self.pass_rate,
            "n_rows": self.n_rows,
            "n_scored_rows": self.n_scored_rows,
            "n_scored_cases": self.n_scored_cases,
            "operational_failure_rate": self.operational_failure_rate,
            "valid_sql_rate": self.valid_sql_rate,
            "cost_usd_accounted": self.cost_usd_accounted,
            "cost_usd_measured": self.cost_usd_measured,
            "n_rows_measured": self.n_rows_measured,
            "cost_usd_for_gate": self.cost_usd_for_gate,
            "cost_source_counts": self.cost_source_counts,
            "safety_failures": self.safety_failures,
            "deployments_used": self.deployments_used,
        }


@dataclass(frozen=True)
class HonestyCounters:
    """Campaign-wide counters that keep the headline pass rate honest."""

    degenerate_candidates: list[str]
    coincidental_match_cases: list[str]
    flaky_cases: list[str]
    cost_source_counts: dict[str, int]
    operational_failure_counts: dict[str, int]
    n_cost_fail_closed: int


def _per_arm_stats(
    arm: str,
    *,
    rows: Sequence[dict[str, Any]],
    scored: Sequence[dict[str, Any]],
    case_ids: Sequence[str],
    pass_rate: Callable[[str, str], float | None],
) -> PerArmStats:
    all_arm_rows = [row for row in rows if str(row["arm"]) == arm]
    arm_rows = [row for row in scored if str(row["arm"]) == arm]
    rates = [rate for case_id in case_ids if (rate := pass_rate(arm, case_id)) is not None]
    deployments = Counter(str(row.get("chat_deployment")) for row in arm_rows)
    arm_cost_sources = Counter(str(row.get("cost_source")) for row in all_arm_rows)
    # Ledger truth (every row, ceiling charges included) vs. a price OBSERVATION
    # (only rows whose token usage was actually captured). The gate divides one arm's
    # cost by the other's, so a ceiling charge -- 8x the priced mean by construction
    # -- would move that ratio by the noise, not the model. The measured mean is
    # scaled back to the arm's full row count so unequal capture coverage between the
    # two arms cannot bias the comparison either.
    measured = [row for row in all_arm_rows if row.get("cost_source") == "usage"]
    cost_measured = sum(float(row.get("cost_usd") or 0.0) for row in measured)
    cost_for_gate = (cost_measured / len(measured)) * len(all_arm_rows) if measured else None
    return PerArmStats(
        pass_rate=round(statistics.mean(rates), 4) if rates else None,
        n_rows=len(all_arm_rows),
        n_scored_rows=len(arm_rows),
        n_scored_cases=len(rates),
        operational_failure_rate=(
            round((len(all_arm_rows) - len(arm_rows)) / len(all_arm_rows), 4)
            if all_arm_rows
            else 0.0
        ),
        valid_sql_rate=(
            round(statistics.mean(bool(row.get("valid_sql")) for row in arm_rows), 4)
            if arm_rows
            else 0.0
        ),
        cost_usd_accounted=round(sum(float(row.get("cost_usd") or 0.0) for row in all_arm_rows), 6),
        cost_usd_measured=cost_measured,
        n_rows_measured=len(measured),
        cost_usd_for_gate=cost_for_gate,
        cost_source_counts=dict(arm_cost_sources),
        safety_failures=sum(1 for row in arm_rows if row_counts_toward_safety_gate(row)),
        deployments_used=dict(deployments),
    )


def _pass_rate_pairs(
    case_ids: Sequence[str],
    *,
    baseline: str,
    candidate: str,
    pass_rate: Callable[[str, str], float | None],
) -> tuple[list[PassRatePair], list[str]]:
    pairs: list[PassRatePair] = []
    unpaired: list[str] = []
    for case_id in case_ids:
        base_rate = pass_rate(baseline, case_id)
        cand_rate = pass_rate(candidate, case_id)
        if base_rate is None or cand_rate is None:
            # A case that lost one arm entirely (every repeat an infra failure) has no
            # comparable pair. It must be NAMED: paired_bootstrap_ci returns lo=0.0 for
            # an empty delta list, so a silently-shrunken population would read as
            # "no regression detected" rather than "almost nothing was measured".
            unpaired.append(case_id)
            continue
        pairs.append(PassRatePair(case_id, base_rate, cand_rate))
    return pairs, unpaired


def _honesty_counters(
    rows: Sequence[dict[str, Any]],
    *,
    case_ids: Sequence[str],
    arms: Sequence[str],
    pass_rate: Callable[[str, str], float | None],
) -> HonestyCounters:
    degenerate = sorted({str(row["case_id"]) for row in rows if row.get("degenerate_candidate")})
    coincidental = sorted(
        {str(row["case_id"]) for row in rows if row.get("coincidental_match_suspected")}
    )
    flaky = sorted(
        {
            case_id
            for arm in arms
            for case_id in case_ids
            if (rate := pass_rate(arm, case_id)) is not None and 0.0 < rate < 1.0
        }
    )
    cost_source_counts = Counter(str(row.get("cost_source")) for row in rows)
    operational_failure_counts = Counter(
        str(row["operational_failure"]) for row in rows if row.get("operational_failure")
    )
    n_cost_fail_closed = sum(
        1 for row in rows if str(row.get("cost_source", "")).startswith("ceiling")
    )
    return HonestyCounters(
        degenerate_candidates=degenerate,
        coincidental_match_cases=coincidental,
        flaky_cases=flaky,
        cost_source_counts=dict(cost_source_counts),
        operational_failure_counts=dict(operational_failure_counts),
        n_cost_fail_closed=n_cost_fail_closed,
    )


def summarize_sql_rows(
    rows: Sequence[dict[str, Any]], frozen: FrozenSqlExperiment
) -> dict[str, Any]:
    """Roll campaign rows up into the gate's inputs plus the honesty counters.

    Every accuracy number is keyed ``execution_accuracy_single_snapshot``. No key in
    this document is called ``accuracy`` -- including inside ``router_policy_replay``,
    which ``finalize`` renames on merge (``_renamed_replay_metric``) because
    ``routing_matrix.score_policy`` speaks generic routing vocabulary and its numbers,
    for this task, carry exactly the same single-snapshot coincidence caveat.
    ``tests/experiments/tasks/test_sql_task.py::
    test_router_replay_never_reports_a_bare_accuracy_key`` locks that, on a freeze that
    actually has a ``router_policy`` -- the shape every production run uses.
    """
    document = frozen.document
    policy = document["policy"]
    arms: list[str] = list(policy["arms"])
    case_ids: list[str] = list(policy["expected_case_ids"])
    baseline, candidate = policy["baseline_arm"], policy["candidate_arm"]
    pass_threshold = float(policy["pass_threshold"])

    # ONE definition of "scored", shared with outcome_cells_from_rows: a row the
    # HARNESS broke is not a model miss. classify_failure calls three things
    # operational (runner_error, infra_error, no_execution_score) and METRIC_NOTE
    # promises all of them are excluded; keeping only the infra_error filter would score
    # a dropped snapshot connection as a wrong answer, manufacture case flips on the
    # candidate arm, and -- on the baseline arm -- make every gate check more permissive.
    def _is_scored(row: dict[str, Any]) -> bool:
        return not row.get("infra_error") and not row.get("operational_failure")

    scored = [row for row in rows if _is_scored(row)]
    by_arm_case: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in scored:
        by_arm_case.setdefault((str(row["arm"]), str(row["case_id"])), []).append(row)

    def _pass_rate(arm: str, case_id: str) -> float | None:
        group = by_arm_case.get((arm, case_id))
        if not group:
            return None
        exec_rows = [row for row in group if row_counts_toward_exec_match(row)]
        if not exec_rows:
            return None
        return sum(1 for row in exec_rows if row.get("match")) / len(exec_rows)

    by_arm = {
        arm: _per_arm_stats(
            arm, rows=rows, scored=scored, case_ids=case_ids, pass_rate=_pass_rate
        ).to_dict()
        for arm in arms
    }

    pairs, unpaired = _pass_rate_pairs(
        case_ids, baseline=baseline, candidate=candidate, pass_rate=_pass_rate
    )
    honesty = _honesty_counters(rows, case_ids=case_ids, arms=arms, pass_rate=_pass_rate)

    pairs_excluding_degenerate = [
        pair for pair in pairs if pair.case_id not in set(honesty.degenerate_candidates)
    ]
    # The sensitivity check is a RESULT, not homework. Including degenerate-gold cases
    # makes the regression check MORE permissive (their delta is ~0, pulling the paired
    # mean toward zero), and scripts/eval/analyze_sql_runs.py carves the same cases out of its
    # headline -- so the two artifacts would otherwise report different accuracy for the
    # same run with nothing saying so.
    sensitivity_ci = paired_bootstrap_ci(
        [pair.candidate - pair.baseline for pair in pairs_excluding_degenerate],
        seed=int(policy.get("bootstrap_seed", BOOTSTRAP_SEED)),
    )

    return {
        "metric": METRIC_NAME,
        "metric_note": METRIC_NOTE,
        "stage": document["stage"],
        "freeze_hash": frozen.freeze_hash,
        "n_cases": len(case_ids),
        "n_arms": len(arms),
        "repeats": int(policy["repeats"]),
        "n_rows": len(rows),
        "n_scored_rows": len(scored),
        # Pooled across arms; the per-arm rates in by_arm[...] are what a reader needs
        # when one arm is failing and the other is not (pooling can hide a 4% arm behind
        # a 0% one).
        "operational_failure_rate": (
            round((len(rows) - len(scored)) / len(rows), 4) if rows else 0.0
        ),
        "operational_failure_counts": honesty.operational_failure_counts,
        "by_arm": by_arm,
        "baseline_arm": baseline,
        "candidate_arm": candidate,
        "pass_threshold": pass_threshold,
        "pass_rate_pairs": pairs,
        # Reported ALONGSIDE the full pair set, never substituted for it: dropping
        # degenerate-gold cases from the gate population silently would redefine the
        # comparison. This is the sensitivity check, not the gate.
        "pass_rate_pairs_excluding_degenerate": pairs_excluding_degenerate,
        "pass_rate_sensitivity_excluding_degenerate": {
            "n": sensitivity_ci["n"],
            "mean_delta": round(sensitivity_ci["point"], 4),
            "ci_lo": round(sensitivity_ci["lo"], 4),
            "ci_hi": round(sensitivity_ci["hi"], 4),
            "note": PASS_RATE_SENSITIVITY_NOTE,
        },
        "cases_without_comparable_pair": unpaired,
        "flaky_cases": honesty.flaky_cases,
        "degenerate_candidates": honesty.degenerate_candidates,
        "coincidental_match_cases": honesty.coincidental_match_cases,
        "safety_failures": sum(int(by_arm[arm]["safety_failures"]) for arm in arms),
        "cost_usd_total": round(sum(float(row.get("cost_usd") or 0.0) for row in rows), 6),
        "cost_source_counts": honesty.cost_source_counts,
        "n_cost_fail_closed": honesty.n_cost_fail_closed,
    }
