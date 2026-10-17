"""Ordered evidence gates for comparative RAG experiments (B3-2)."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.eval.run_stats import paired_bootstrap_ci
from app.experiments.core.protocol import OracleValidation
from app.experiments.rag_experiment import RagEvidenceVerdict, RagGatePolicy


def _expected_provider_by_arm(policy: RagGatePolicy) -> dict[str, str]:
    return {arm: provider for arm, provider, _model in policy.expected_requested_routes}


GateFn = Callable[["EvidenceView"], RagEvidenceVerdict | None]


@dataclass(frozen=True)
class EvidenceView:
    """Every fact a gate can decide from, computed once, eagerly, over the whole input.

    Every field is computed up front in :meth:`from_evidence` -- total over every
    input, including empty rows and a malformed policy. That totality is what
    lets every gate run on every call (Ruling 1): a gate reached only after an
    earlier one cleared the way still runs unconditionally, so nothing it reads
    may be conditionally absent. Each of the 18 gates in ``_GATES`` takes exactly
    this view and reads ``view.<field>`` -- none may compute, mutate, or
    otherwise reach outside it.
    """

    rows: tuple[dict[str, Any], ...]
    policy: RagGatePolicy
    judge_validation: OracleValidation
    expected_rows: int
    expected_providers: dict[str, str]
    operational_rows: tuple[dict[str, Any], ...]
    harness_cap_rows: tuple[dict[str, Any], ...]
    provider_cap_rows: tuple[dict[str, Any], ...]
    starvation_rows: tuple[dict[str, Any], ...]
    all_cap_related_rows: tuple[dict[str, Any], ...]
    missing_score_values: int
    invalid_score_rows: tuple[dict[str, Any], ...]
    evidence_details: dict[str, Any]
    failed_cells: frozenset[tuple[str, int]]
    metric_decisions: dict[str, dict[str, Any]]
    has_inferior_metric: bool
    has_inconclusive_metric: bool
    has_short_paired_cases: bool
    details: dict[str, Any]

    @classmethod
    def from_evidence(
        cls,
        rows: list[dict[str, Any]],
        *,
        policy: RagGatePolicy,
        judge_validation: OracleValidation,
    ) -> EvidenceView:
        expected_providers = _expected_provider_by_arm(policy)
        expected_rows = len(policy.expected_case_ids) * len(policy.answer_arms) * policy.repeats

        operational_rows = [row for row in rows if row.get("operational_failure")]
        harness_cap_rows = [
            row
            for row in operational_rows
            if row.get("operational_failure") in {"harness_output_cap", "output_ceiling"}
        ]
        provider_cap_rows = [
            row
            for row in operational_rows
            if row.get("operational_failure") == "provider_output_cap"
        ]
        starvation_rows = [
            row
            for row in operational_rows
            if row.get("operational_failure") == "reasoning_budget_starvation"
        ]
        all_cap_related_rows = harness_cap_rows + provider_cap_rows + starvation_rows

        missing_score_values = 0
        invalid_score_rows: list[dict[str, Any]] = []
        for row in rows:
            if row.get("operational_failure"):
                continue
            for metric in policy.metrics:
                try:
                    score = float(row.get(metric))
                except (TypeError, ValueError):
                    missing_score_values += 1
                    continue
                if not math.isfinite(score):
                    missing_score_values += 1
                elif not 0.0 <= score <= 1.0:
                    invalid_score_rows.append(
                        {
                            "case_id": row.get("case_id"),
                            "arm": row.get("arm"),
                            "repeat": row.get("repeat"),
                            "metric": metric,
                            "value": score,
                        }
                    )

        answer_opportunities = expected_rows
        score_opportunities = expected_rows * len(policy.metrics)
        operational_failure_rate = (
            len(operational_rows) / answer_opportunities if answer_opportunities else 0.0
        )
        missing_score_rate = (
            missing_score_values / score_opportunities if score_opportunities else 0.0
        )
        failed_cells = frozenset(
            (str(row.get("case_id")), int(row.get("repeat")))
            for row in rows
            if row.get("operational_failure")
        )
        dropped_pairs = sorted(failed_cells)
        paired_n = sum(
            1
            for case_id in policy.expected_case_ids
            for repeat in range(policy.repeats)
            if (case_id, repeat) not in failed_cells
        )
        evidence_details: dict[str, Any] = {
            "operational_failures": len(operational_rows),
            "harness_output_cap_failures": len(harness_cap_rows),
            "provider_output_cap_failures": len(provider_cap_rows),
            "reasoning_budget_starvation_failures": len(starvation_rows),
            "output_ceiling_failures": len(all_cap_related_rows),
            "missing_score_values": missing_score_values,
            "zeroed_operational_score_values": len(operational_rows) * len(policy.metrics),
            "answer_opportunities": answer_opportunities,
            "score_opportunities": score_opportunities,
            "operational_failure_rate": operational_failure_rate,
            "missing_score_rate": missing_score_rate,
            "opportunity_failure_rate": max(operational_failure_rate, missing_score_rate),
            "dropped_pairs": dropped_pairs,
            "paired_n": paired_n,
        }

        # Guarded exactly as the eager-evaluation audit requires: an incomplete or
        # empty arm policy must never reach the two-way unpack below.
        metric_decisions: dict[str, dict[str, Any]] = {}
        has_inferior_metric = False
        has_inconclusive_metric = False
        has_short_paired_cases = False
        if len(policy.answer_arms) == 2 and rows:
            base_arm, candidate_arm = policy.answer_arms
            for metric in policy.metrics:
                scores: dict[tuple[str, str, int], float] = {}
                for row in rows:
                    if row.get("operational_failure"):
                        continue
                    try:
                        score = float(row.get(metric))
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(score):
                        scores[(str(row["case_id"]), str(row["arm"]), int(row["repeat"]))] = score

                deltas: list[float] = []
                for case_id in policy.expected_case_ids:
                    for repeat in range(policy.repeats):
                        if (case_id, repeat) in failed_cells:
                            continue
                        base_score = scores.get((case_id, base_arm, repeat))
                        candidate_score = scores.get((case_id, candidate_arm, repeat))
                        if base_score is None or candidate_score is None:
                            continue
                        deltas.append(candidate_score - base_score)

                ci = paired_bootstrap_ci(deltas, seed=policy.bootstrap_seed)
                if ci["n"] < paired_n:
                    has_short_paired_cases = True
                if ci["lo"] >= -policy.regression_tolerance:
                    decision = "noninferior"
                elif ci["hi"] < -policy.regression_tolerance:
                    decision = "inferior"
                    has_inferior_metric = True
                else:
                    decision = "inconclusive"
                    has_inconclusive_metric = True
                metric_decisions[metric] = {
                    "n": ci["n"],
                    "mean_delta": ci["point"],
                    "ci_lo": ci["lo"],
                    "ci_hi": ci["hi"],
                    "decision": decision,
                }

        details = {**evidence_details, **metric_decisions}

        return cls(
            rows=tuple(rows),
            policy=policy,
            judge_validation=judge_validation,
            expected_rows=expected_rows,
            expected_providers=expected_providers,
            operational_rows=tuple(operational_rows),
            harness_cap_rows=tuple(harness_cap_rows),
            provider_cap_rows=tuple(provider_cap_rows),
            starvation_rows=tuple(starvation_rows),
            all_cap_related_rows=tuple(all_cap_related_rows),
            missing_score_values=missing_score_values,
            invalid_score_rows=tuple(invalid_score_rows),
            evidence_details=evidence_details,
            failed_cells=failed_cells,
            metric_decisions=metric_decisions,
            has_inferior_metric=has_inferior_metric,
            has_inconclusive_metric=has_inconclusive_metric,
            has_short_paired_cases=has_short_paired_cases,
            details=details,
        )


def _check_no_evidence(view: EvidenceView) -> RagEvidenceVerdict | None:
    if not view.rows:
        return RagEvidenceVerdict(status="HOLD", reason_codes=("no_evidence",))
    return None


def _check_incomplete_matrix(view: EvidenceView) -> RagEvidenceVerdict | None:
    if len(view.rows) != view.expected_rows:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("incomplete_matrix",),
            details={"expected_rows": view.expected_rows, "observed_rows": len(view.rows)},
        )
    return None


def _check_matrix_integrity(view: EvidenceView) -> RagEvidenceVerdict | None:
    policy = view.policy
    expected_cells = {
        (case_id, arm, repeat)
        for case_id in policy.expected_case_ids
        for arm in policy.answer_arms
        for repeat in range(policy.repeats)
    }
    observed_cells = [
        (str(row.get("case_id")), str(row.get("arm")), row.get("repeat")) for row in view.rows
    ]
    counts = Counter(observed_cells)
    duplicate_cells = sorted(cell for cell, count in counts.items() if count > 1)
    missing_cells = sorted(expected_cells - set(observed_cells))
    unexpected_cells = sorted(set(observed_cells) - expected_cells)
    matrix_reasons: list[str] = []
    if duplicate_cells:
        matrix_reasons.append("duplicate_matrix_cell")
    if missing_cells:
        matrix_reasons.append("missing_matrix_cell")
    if unexpected_cells:
        matrix_reasons.append("unexpected_matrix_cell")
    if matrix_reasons:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=tuple(matrix_reasons),
            details={
                "duplicate_cells": duplicate_cells,
                "missing_cells": missing_cells,
                "unexpected_cells": unexpected_cells,
            },
        )
    return None


def _check_context_mismatch(view: EvidenceView) -> RagEvidenceVerdict | None:
    context_hashes = {
        case_id: {
            str(row.get("context_hash")) for row in view.rows if row.get("case_id") == case_id
        }
        for case_id in view.policy.expected_case_ids
    }
    mismatched_cases = sorted(
        case_id
        for case_id, hashes in context_hashes.items()
        if len(hashes) != 1 or "None" in hashes or "" in hashes
    )
    if mismatched_cases:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("context_mismatch",),
            details={"mismatched_context_cases": mismatched_cases},
        )
    return None


def _check_response_metadata_incomplete(view: EvidenceView) -> RagEvidenceVerdict | None:
    required_response_metadata = (
        "requested_provider",
        "requested_model",
        "actual_provider",
        "actual_provider_source",
        "actual_model",
        "actual_model_source",
        "request_id",
        "request_id_source",
        "finish_reason",
        "latency_ms",
        "input_tokens",
        "output_tokens",
    )
    metadata_incomplete_rows: list[dict[str, Any]] = []
    for row in view.rows:
        if row.get("operational_failure"):
            continue
        missing = [
            field_name
            for field_name in required_response_metadata
            if row.get(field_name) is None or row.get(field_name) == ""
        ]
        if missing:
            metadata_incomplete_rows.append(
                {
                    "case_id": row.get("case_id"),
                    "arm": row.get("arm"),
                    "repeat": row.get("repeat"),
                    "missing": missing,
                }
            )
    if metadata_incomplete_rows:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("response_metadata_incomplete",),
            details={"metadata_incomplete_rows": metadata_incomplete_rows},
        )
    return None


def _check_provider_request_id_unverified(view: EvidenceView) -> RagEvidenceVerdict | None:
    unverified_request_id_rows = [
        {
            "case_id": row.get("case_id"),
            "arm": row.get("arm"),
            "repeat": row.get("repeat"),
            "request_id_source": row.get("request_id_source"),
        }
        for row in view.rows
        if not row.get("operational_failure") and row.get("request_id_source") != "provider"
    ]
    if unverified_request_id_rows:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("provider_request_id_unverified",),
            details={"unverified_request_id_rows": unverified_request_id_rows},
        )
    return None


def _check_requested_route_mismatch(view: EvidenceView) -> RagEvidenceVerdict | None:
    policy = view.policy
    expected_routes = {
        arm: (provider, model) for arm, provider, model in policy.expected_requested_routes
    }
    requested_route_mismatches = [
        {
            "case_id": row.get("case_id"),
            "arm": row.get("arm"),
            "repeat": row.get("repeat"),
            "expected": expected_routes.get(str(row.get("arm"))),
            "observed": (row.get("requested_provider"), row.get("requested_model")),
        }
        for row in view.rows
        if str(row.get("arm")) in expected_routes
        and (
            row.get("requested_provider"),
            row.get("requested_model"),
        )
        != expected_routes[str(row.get("arm"))]
    ]
    if requested_route_mismatches:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("requested_route_mismatch",),
            details={"requested_route_mismatches": requested_route_mismatches},
        )
    return None


def _check_route_metadata_unverified(view: EvidenceView) -> RagEvidenceVerdict | None:
    expected_providers = view.expected_providers
    unverified_route_rows = [
        {
            "case_id": row.get("case_id"),
            "arm": row.get("arm"),
            "repeat": row.get("repeat"),
            "status": row.get("route_metadata_status"),
            "error": row.get("route_metadata_error"),
        }
        for row in view.rows
        if not row.get("operational_failure")
        and expected_providers.get(str(row.get("arm"))) == "openrouter"
        and row.get("route_metadata_status") != "verified"
    ]
    if unverified_route_rows:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("route_metadata_unverified",),
            details={"unverified_route_rows": unverified_route_rows},
        )
    return None


def _check_provider_route_outside_freeze(view: EvidenceView) -> RagEvidenceVerdict | None:
    policy = view.policy
    if not (policy.candidate_route_providers or policy.route_providers_by_arm):
        return None
    expected_providers = view.expected_providers
    providers_by_arm = dict(policy.route_providers_by_arm)
    outside_route_rows = [
        {
            "case_id": row.get("case_id"),
            "arm": row.get("arm"),
            "repeat": row.get("repeat"),
            "actual_provider": row.get("actual_provider"),
        }
        for row in view.rows
        if not row.get("operational_failure")
        and expected_providers.get(str(row.get("arm"))) == "openrouter"
        and row.get("actual_provider")
        not in (providers_by_arm.get(str(row.get("arm")), ()) or policy.candidate_route_providers)
    ]
    if outside_route_rows:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("provider_route_outside_freeze",),
            details={
                "approved_providers": list(policy.candidate_route_providers),
                "outside_route_rows": outside_route_rows,
            },
        )
    return None


def _check_actual_model_mismatch(view: EvidenceView) -> RagEvidenceVerdict | None:
    policy = view.policy
    expected_providers = view.expected_providers
    allowed_actual_models = dict(policy.allowed_actual_models)
    actual_model_mismatches = [
        {
            "case_id": row.get("case_id"),
            "arm": row.get("arm"),
            "repeat": row.get("repeat"),
            "requested_model": row.get("requested_model"),
            "actual_model": row.get("actual_model"),
            "actual_model_source": row.get("actual_model_source"),
            "allowed_actual_models": list(allowed_actual_models.get(str(row.get("arm")), ())),
        }
        for row in view.rows
        if not row.get("operational_failure")
        and (
            row.get("actual_model_source")
            not in (
                {"route_metadata"}
                if expected_providers.get(str(row.get("arm"))) == "openrouter"
                else {"provider_response", "route_metadata"}
            )
            or row.get("actual_model") not in allowed_actual_models.get(str(row.get("arm")), ())
        )
    ]
    if actual_model_mismatches:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("actual_model_mismatch",),
            details={"actual_model_mismatches": actual_model_mismatches},
        )
    return None


def _check_judge_validation(view: EvidenceView) -> RagEvidenceVerdict | None:
    judge_validation = view.judge_validation
    judge_reasons: list[str] = []
    if not judge_validation.canaries_passed:
        judge_reasons.append("judge_canary_failed")
    if not judge_validation.repeatability_passed:
        judge_reasons.append("judge_repeatability_failed")
    if not judge_validation.call_evidence_passed:
        judge_reasons.append("judge_call_evidence_failed")
    if judge_reasons:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=tuple(judge_reasons),
            details={"judge_validation": judge_validation.details},
        )
    return None


def _check_actual_provider_unattested(view: EvidenceView) -> RagEvidenceVerdict | None:
    expected_providers = view.expected_providers
    provider_source_mismatches = [
        {
            "case_id": row.get("case_id"),
            "arm": row.get("arm"),
            "repeat": row.get("repeat"),
            "actual_provider": row.get("actual_provider"),
            "actual_provider_source": row.get("actual_provider_source"),
        }
        for row in view.rows
        if not row.get("operational_failure")
        and (
            (
                expected_providers.get(str(row.get("arm"))) == "openrouter"
                and row.get("actual_provider_source") != "route_metadata"
            )
            or (
                expected_providers.get(str(row.get("arm"))) == "azure"
                and row.get("actual_provider_source")
                not in {"provider_response", "azure_client_type_endpoint_and_deployment"}
            )
        )
    ]
    if provider_source_mismatches:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("actual_provider_unattested",),
            details={"actual_provider_unattested_rows": provider_source_mismatches},
        )
    return None


def _check_invalid_score_value(view: EvidenceView) -> RagEvidenceVerdict | None:
    if view.invalid_score_rows:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("invalid_score_value",),
            details={"invalid_score_rows": list(view.invalid_score_rows)},
        )
    return None


def _check_opportunity_failure_rates(view: EvidenceView) -> RagEvidenceVerdict | None:
    policy = view.policy
    operational_failure_rate = view.evidence_details["operational_failure_rate"]
    missing_score_rate = view.evidence_details["missing_score_rate"]
    exceeded_reasons: list[str] = []
    if operational_failure_rate > policy.max_opportunity_failure_rate:
        exceeded_reasons.append("operational_failure_rate_exceeded")
    if missing_score_rate > policy.max_opportunity_failure_rate:
        exceeded_reasons.append("missing_score_rate_exceeded")
    if exceeded_reasons:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=tuple(exceeded_reasons),
            details=view.evidence_details,
        )
    return None


def _check_insufficient_paired_cases(view: EvidenceView) -> RagEvidenceVerdict | None:
    if view.has_short_paired_cases:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("insufficient_paired_cases",),
            details=view.details,
        )
    return None


def _check_quality_regression(view: EvidenceView) -> RagEvidenceVerdict | None:
    if view.has_inferior_metric:
        return RagEvidenceVerdict(
            status="FAIL",
            reason_codes=("quality_regression",),
            details=view.details,
        )
    return None


def _check_quality_inconclusive(view: EvidenceView) -> RagEvidenceVerdict | None:
    if view.has_inconclusive_metric:
        return RagEvidenceVerdict(
            status="HOLD",
            reason_codes=("quality_inconclusive",),
            details=view.details,
        )
    return None


def _check_pass(view: EvidenceView) -> RagEvidenceVerdict | None:
    return RagEvidenceVerdict(
        status="RAG-CANDIDATE-DEV",
        reason_codes=(),
        details=view.details,
    )


_GATES: tuple[GateFn, ...] = (
    _check_no_evidence,
    _check_incomplete_matrix,
    _check_matrix_integrity,
    _check_context_mismatch,
    _check_response_metadata_incomplete,
    _check_provider_request_id_unverified,
    _check_requested_route_mismatch,
    _check_route_metadata_unverified,
    _check_provider_route_outside_freeze,
    _check_actual_model_mismatch,
    _check_judge_validation,
    _check_actual_provider_unattested,
    _check_invalid_score_value,
    _check_opportunity_failure_rates,
    _check_insufficient_paired_cases,
    _check_quality_regression,
    _check_quality_inconclusive,
    _check_pass,
)

#: Rank decides which firing gate wins; the tuple's order does not. Gates are
#: pure predicates over a frozen view, so evaluating all of them is safe and
#: the winner is the lowest rank, not the first match.
_GATE_RANKS: dict[str, int] = {gate.__name__: rank for rank, gate in enumerate(_GATES)}


def evaluate_rag_evidence(
    rows: list[dict[str, Any]],
    *,
    policy: RagGatePolicy,
    judge_validation: OracleValidation,
) -> RagEvidenceVerdict:
    """Validate attempt evidence before making any model-quality claim.

    Every gate runs, over one frozen EvidenceView; the firing gate with the
    lowest rank wins. Ranks are seeded to _GATES' declaration order, so today's
    verdicts are unchanged, but the winner no longer depends on _GATES' order at
    runtime -- shuffling the tuple cannot change which verdict is returned.
    """
    view = EvidenceView.from_evidence(rows, policy=policy, judge_validation=judge_validation)
    fired = [
        (_GATE_RANKS[gate.__name__], verdict)
        for gate in _GATES
        if (verdict := gate(view)) is not None
    ]
    if not fired:
        raise AssertionError("evaluate_rag_evidence: no gate returned a verdict")
    return min(fired, key=lambda pair: pair[0])[1]
