"""Deterministic validation of synthetic canaries and repeated judge scores."""

from __future__ import annotations

import math
from typing import Any

from app.experiments.core.protocol import OracleValidation

JUDGE_VALIDATION_MAX_MEAN_ABS_DELTA = 0.10
MAX_LARGE_DISAGREEMENT_RATE = 0.10
LARGE_DISAGREEMENT_DELTA = 0.20
MAX_ARM_DELTA_SHIFT = 0.05
METRICS = ("factual_correctness", "faithfulness")


def _finite(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) else None


def validate_canary_scores(
    scores: list[dict[str, Any]],
    expectations: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    by_id = {str(row.get("id")): row for row in scores}
    failures: list[dict[str, Any]] = []
    for canary_id, bounds in expectations.items():
        row = by_id.get(canary_id)
        if row is None:
            failures.append({"id": canary_id, "reason": "missing_canary"})
            continue
        for bound_name, bound in bounds.items():
            metric, direction = bound_name.rsplit("_", 1)
            value = _finite(row.get(metric))
            if value is None:
                failures.append({"id": canary_id, "metric": metric, "reason": "non_finite"})
            elif not 0.0 <= value <= 1.0:
                failures.append(
                    {
                        "id": canary_id,
                        "metric": metric,
                        "reason": "outside_unit_interval",
                        "value": value,
                    }
                )
            elif direction == "min" and value < bound:
                failures.append(
                    {
                        "id": canary_id,
                        "metric": metric,
                        "reason": "below_min",
                        "value": value,
                        "bound": bound,
                    }
                )
            elif direction == "max" and value > bound:
                failures.append(
                    {
                        "id": canary_id,
                        "metric": metric,
                        "reason": "above_max",
                        "value": value,
                        "bound": bound,
                    }
                )
    return failures


def validate_judge_call_evidence(
    call_evidence: list[dict[str, Any]],
    *,
    required_passes: tuple[str, ...],
    judge_version: dict[str, Any],
) -> list[dict[str, Any]]:
    """Require provider-level identity, finish and usage for every requested pass."""
    failures: list[dict[str, Any]] = []
    if not call_evidence:
        failures.append({"reason": "no_judge_call_evidence"})
    observed_passes = {str(row.get("pass")) for row in call_evidence}
    for pass_name in required_passes:
        if pass_name not in observed_passes:
            failures.append({"reason": "missing_judge_pass", "pass": pass_name})
    attestation = judge_version.get("deployment_attestation") or {}
    expected_provider = judge_version.get("provider")
    expected_model = judge_version.get("model")
    expected_endpoint = attestation.get("endpoint_host")
    allowed_actual_models = set(attestation.get("allowed_response_models") or ())
    if not (
        expected_provider
        and expected_model
        and expected_endpoint
        and allowed_actual_models
        and attestation.get("source") == "azure_resource_manager"
    ):
        failures.append({"reason": "missing_frozen_judge_deployment_attestation"})
    observed_actual_models: set[str] = set()
    for index, row in enumerate(call_evidence):
        missing: list[str] = []
        if not row.get("request_id"):
            missing.append("request_id")
        if row.get("request_id_source") != "provider":
            missing.append("provider_request_id")
        if row.get("actual_model") in {None, "", "unknown"}:
            missing.append("actual_model")
        else:
            observed_actual_models.add(str(row["actual_model"]))
        if row.get("finish_reason") in {None, "", "unknown"}:
            missing.append("finish_reason")
        if int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0) <= 0:
            missing.append("usage")
        if row.get("error"):
            missing.append("error_free_completion")
        if row.get("requested_provider") != expected_provider:
            missing.append("requested_provider_mismatch")
        if row.get("requested_model") != expected_model:
            missing.append("requested_model_mismatch")
        if row.get("actual_provider") != expected_provider:
            missing.append("actual_provider_mismatch")
        if row.get("actual_provider_source") != "azure_client_type_and_base_url":
            missing.append("actual_provider_unattested")
        if row.get("endpoint_host") != expected_endpoint:
            missing.append("endpoint_mismatch")
        if row.get("actual_model") not in allowed_actual_models:
            missing.append("actual_model_outside_deployment_attestation")
        if missing:
            failures.append({"index": index, "missing_or_invalid": missing})
    if len(observed_actual_models) > 1:
        failures.append(
            {
                "reason": "judge_actual_model_changed_between_passes",
                "actual_models": sorted(observed_actual_models),
            }
        )
    return failures


def _score_map(
    rows: list[dict[str, Any]], subset_case_ids: set[str], metric: str
) -> dict[tuple[str, str, int], float]:
    scores: dict[tuple[str, str, int], float] = {}
    for row in rows:
        case_id = str(row.get("case_id"))
        if case_id not in subset_case_ids:
            continue
        value = _finite(row.get(metric))
        if value is None:
            continue
        scores[(case_id, str(row.get("arm")), int(row.get("repeat")))] = value
    return scores


def _arm_delta(
    scores: dict[tuple[str, str, int], float],
    *,
    arms: tuple[str, str],
) -> float | None:
    """Candidate mean minus baseline mean, or None when either arm has no scores."""
    by_arm: dict[str, list[float]] = {}
    for (_case_id, arm, _repeat), value in scores.items():
        by_arm.setdefault(arm, []).append(value)
    base = by_arm.get(arms[0]) or []
    candidate = by_arm.get(arms[1]) or []
    if not base or not candidate:
        return None
    return sum(candidate) / len(candidate) - sum(base) / len(base)


def validate_judge_evidence(
    *,
    canary_scores: list[dict[str, Any]],
    canary_expectations: dict[str, dict[str, float]],
    primary_rows: list[dict[str, Any]],
    secondary_rows: list[dict[str, Any]],
    subset_case_ids: tuple[str, ...],
    arms: tuple[str, str],
    repeats: int,
    judge_version: dict[str, Any],
    cost_usd: float,
    call_evidence: list[dict[str, Any]],
) -> OracleValidation:
    """Return judge health independently of the candidate-vs-baseline result."""
    canary_failures = validate_canary_scores(canary_scores, canary_expectations)
    call_evidence_failures = validate_judge_call_evidence(
        call_evidence,
        required_passes=("canary", "primary", "repeatability"),
        judge_version=judge_version,
    )
    subset = set(subset_case_ids)
    repeatability: dict[str, Any] = {}
    repeatability_passed = True
    for metric in METRICS:
        primary = _score_map(primary_rows, subset, metric)
        secondary = _score_map(secondary_rows, subset, metric)
        expected_keys = {
            (case_id, arm, repeat)
            for case_id in subset_case_ids
            for arm in arms
            for repeat in range(repeats)
        }
        common_keys = expected_keys & set(primary) & set(secondary)
        coverage = len(common_keys) / len(expected_keys) if expected_keys else 0.0
        differences = [abs(primary[key] - secondary[key]) for key in sorted(common_keys)]
        mean_abs_delta = sum(differences) / len(differences) if differences else math.inf
        large_disagreement_rate = (
            sum(delta > LARGE_DISAGREEMENT_DELTA for delta in differences) / len(differences)
            if differences
            else 1.0
        )
        primary_arm_delta = _arm_delta(primary, arms=arms)
        secondary_arm_delta = _arm_delta(secondary, arms=arms)
        arm_delta_shift = (
            abs(primary_arm_delta - secondary_arm_delta)
            if primary_arm_delta is not None and secondary_arm_delta is not None
            else math.inf
        )
        passed = (
            coverage == 1.0
            and mean_abs_delta <= JUDGE_VALIDATION_MAX_MEAN_ABS_DELTA
            and large_disagreement_rate <= MAX_LARGE_DISAGREEMENT_RATE
            and arm_delta_shift <= MAX_ARM_DELTA_SHIFT
        )
        repeatability_passed = repeatability_passed and passed
        repeatability[metric] = {
            "expected_scores": len(expected_keys),
            "observed_second_pass_scores": len(common_keys),
            "coverage": coverage,
            "mean_abs_delta": round(mean_abs_delta, 6),
            "large_disagreement_rate": round(large_disagreement_rate, 6),
            "arm_delta_shift": round(arm_delta_shift, 6),
            "passed": passed,
        }

    return OracleValidation(
        canaries_passed=not canary_failures,
        repeatability_passed=repeatability_passed,
        call_evidence_passed=not call_evidence_failures,
        details={
            "judge_version": judge_version,
            "canary_failures": canary_failures,
            "repeatability": repeatability,
            "thresholds": {
                "max_mean_abs_delta": JUDGE_VALIDATION_MAX_MEAN_ABS_DELTA,
                "max_large_disagreement_rate": MAX_LARGE_DISAGREEMENT_RATE,
                "large_disagreement_delta": LARGE_DISAGREEMENT_DELTA,
                "max_arm_delta_shift": MAX_ARM_DELTA_SHIFT,
            },
            "call_evidence": call_evidence,
            "call_evidence_failures": call_evidence_failures,
            "cost_usd": cost_usd,
        },
    )
