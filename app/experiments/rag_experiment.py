"""Immutable, deterministic evidence pipeline for comparative RAG experiments."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.experiments.core.errors import RagExperimentError as _RagExperimentError
from app.experiments.core.frozen_contract import verify_frozen_contract as _verify_frozen_contract
from app.experiments.core.money import BUDGET_EPSILON_USD
from app.experiments.core.runtime_identity import RuntimeIdentity
from app.experiments.freeze_projection import merge_profile_projection_onto_arm_blob
from app.experiments.manifest import DEEPSEEK_ACTUAL_MODELS

FREEZE_RETRIEVAL_MAX_WORKERS = 8

__all__ = [
    "AnswerArm",
    "FREEZE_RETRIEVAL_MAX_WORKERS",
    "FrozenRagExperiment",
    "RagEvidenceVerdict",
    "RagGatePolicy",
    "RuntimeIdentity",
    "answer_evidence_failures",
    "classify_answer_failure",
    "enforce_operational_budget",
    "freeze_rag_experiment",
    "infer_output_ceiling_is_harness_cap",
    "refresh_operational_failure_labels",
    "verify_frozen_contract",
]


@dataclass(frozen=True)
class AnswerArm:
    """One explicitly requested answer route in the primary comparison."""

    name: str
    requested_provider: str
    requested_model: str
    reasoning_effort: str | None = None
    verbosity: str | None = None
    request_timeout_s: float | None = None
    apply_reasoning_discipline: bool = False
    harness_output_cap_tokens: int | None = None
    attested_output_cap_tokens: int | None = None
    profile_id: str | None = None
    deployment_attestation: dict[str, Any] | None = None
    est_answer_usd: float | None = None


@dataclass(frozen=True)
class FrozenRagExperiment:
    """In-memory handle to the immutable JSON contract on disk."""

    freeze_hash: str
    output_path: Path
    document: dict[str, Any]


@dataclass(frozen=True)
class RagGatePolicy:
    """Frozen structural and statistical rules for one comparison.

    Arm-identity fields have no defaults — build via ``from_policy_doc`` from a freeze.
    """

    expected_case_ids: tuple[str, ...]
    answer_arms: tuple[str, str]
    repeats: int = 3
    metrics: tuple[str, str] = ("factual_correctness", "faithfulness")
    max_opportunity_failure_rate: float = 0.02
    regression_tolerance: float = 0.05
    bootstrap_seed: int = 7
    candidate_route_providers: tuple[str, ...] = ()
    route_providers_by_arm: tuple[tuple[str, tuple[str, ...]], ...] = ()
    expected_requested_routes: tuple[tuple[str, str, str], ...] = ()
    allowed_actual_models: tuple[tuple[str, tuple[str, ...]], ...] = ()
    harness_output_cap_tokens_by_arm: tuple[tuple[str, int], ...] = ()
    output_ceiling_is_harness_cap: bool = True
    candidate_reasoning_effort: str = "low"

    @classmethod
    def from_policy_doc(cls, policy_doc: dict[str, Any]) -> RagGatePolicy:
        """The ONE reconstruction of a frozen policy document."""
        return cls(
            expected_case_ids=tuple(policy_doc["expected_case_ids"]),
            answer_arms=tuple(policy_doc["answer_arms"]),
            repeats=int(policy_doc["repeats"]),
            metrics=tuple(policy_doc["metrics"]),
            max_opportunity_failure_rate=float(policy_doc["max_opportunity_failure_rate"]),
            regression_tolerance=float(policy_doc["regression_tolerance"]),
            bootstrap_seed=int(policy_doc["bootstrap_seed"]),
            candidate_route_providers=tuple(policy_doc.get("candidate_route_providers") or ()),
            route_providers_by_arm=tuple(
                (arm, tuple(providers))
                for arm, providers in (policy_doc.get("route_providers_by_arm") or {}).items()
            ),
            expected_requested_routes=tuple(
                tuple(route) for route in policy_doc.get("expected_requested_routes") or ()
            ),
            allowed_actual_models=tuple(
                (arm, tuple(models))
                for arm, models in (policy_doc.get("allowed_actual_models") or {}).items()
            ),
            harness_output_cap_tokens_by_arm=tuple(
                (arm, int(cap))
                for arm, cap in (policy_doc.get("harness_output_cap_tokens_by_arm") or {}).items()
            ),
            output_ceiling_is_harness_cap=bool(
                policy_doc.get("output_ceiling_is_harness_cap", True)
            ),
            candidate_reasoning_effort=str(policy_doc.get("candidate_reasoning_effort") or "low"),
        )


@dataclass(frozen=True)
class RagEvidenceVerdict:
    """Deterministic interpretation of one immutable attempt's evidence."""

    status: str
    reason_codes: tuple[str, ...]
    details: dict[str, Any] = field(default_factory=dict)


def freeze_rag_experiment(
    *,
    experiment_id: str,
    cases: list[dict[str, Any]],
    retrieve_fn: Callable[[dict[str, Any]], Any],
    runtime: RuntimeIdentity,
    output_path: Path,
    created_at: str,
    arms: tuple[AnswerArm, AnswerArm] = (
        AnswerArm(
            "prod-mini",
            "azure",
            "gpt-4o-mini",
            harness_output_cap_tokens=16_384,
        ),
        AnswerArm(
            "forced-deepseek",
            "openrouter",
            "deepseek/deepseek-v4-flash-0731",
            harness_output_cap_tokens=32_768,
            attested_output_cap_tokens=65_536,
        ),
    ),
    judge: dict[str, Any] | None = None,
    route_policy: dict[str, Any] | None = None,
    route_policy_by_arm: dict[str, dict[str, Any]] | None = None,
    route_providers_by_arm: dict[str, tuple[str, ...]] | None = None,
    artifact_identity: dict[str, str] | None = None,
    environment_evidence: dict[str, Any] | None = None,
    candidate_route_providers: tuple[str, ...] = ("DeepInfra",),
    allowed_actual_models: dict[str, tuple[str, ...]] | None = None,
    harness_output_cap_tokens_by_arm: dict[str, int] | None = None,
    candidate_reasoning_effort: str | None = "low",
    output_ceiling_is_harness_cap: bool | None = None,
    campaign_cap_usd: float = 8.0,
    spend_stages: dict[str, Any] | None = None,
    answer_order_seed: int = 99173,
    retrieve_max_workers: int = FREEZE_RETRIEVAL_MAX_WORKERS,
    oracle_is_paid: bool = True,
    baseline_profile_id: str | None = None,
    candidate_profile_id: str | None = None,
    arm_profile_projections: dict[str, dict[str, object]] | None = None,
) -> FrozenRagExperiment:
    """Freeze complete inputs and retrieval evidence before answer spend.

    ``oracle_is_paid`` defaults True (existing callers). Pass False for oracles that
    skip canary/judge preflight; then ``canary_reserve_usd`` and ``judge_reserve_usd``
    are optional. The other staged-spend fields (``answer_reserve_usd``,
    ``answer_cell_ceiling_usd``, ``answer_cell_forecast_usd``) are task-agnostic and
    stay required either way.
    """
    if runtime.dirty:
        raise _RagExperimentError("dirty runtime: commit and push exact code before freezing")
    if not isinstance(retrieve_max_workers, int) or retrieve_max_workers < 1:
        raise _RagExperimentError("retrieve_max_workers must be a positive integer")
    if output_path.exists():
        raise _RagExperimentError(f"frozen experiment already exists: {output_path}")
    if len(arms) != 2 or len({arm.name for arm in arms}) != 2:
        raise _RagExperimentError("primary RAG comparison requires exactly two unique arms")
    if not math.isfinite(campaign_cap_usd) or campaign_cap_usd <= 0:
        raise _RagExperimentError("campaign cap must be finite and positive")
    if spend_stages is not None:
        required_spend_fields = {
            "answer_reserve_usd",
            "answer_cell_ceiling_usd",
            "answer_cell_forecast_usd",
        }
        reservation_fields = ["answer_reserve_usd"]
        if oracle_is_paid:
            required_spend_fields |= {"canary_reserve_usd", "judge_reserve_usd"}
            reservation_fields += ["canary_reserve_usd", "judge_reserve_usd"]
        missing_spend_fields = sorted(required_spend_fields - spend_stages.keys())
        if missing_spend_fields:
            raise _RagExperimentError(f"spend stages missing fields: {missing_spend_fields}")
        for field_name in reservation_fields:
            value = float(spend_stages[field_name])
            if not math.isfinite(value) or value <= 0 or value > campaign_cap_usd:
                raise _RagExperimentError(f"invalid staged reservation: {field_name}")
        ceilings = spend_stages["answer_cell_ceiling_usd"]
        if not isinstance(ceilings, dict) or set(ceilings) != {arm.name for arm in arms}:
            raise _RagExperimentError("answer cell ceilings must cover both frozen arms")
        if any(not math.isfinite(float(value)) or float(value) <= 0 for value in ceilings.values()):
            raise _RagExperimentError("answer cell ceilings must be finite and positive")
        forecasts = spend_stages["answer_cell_forecast_usd"]
        if not isinstance(forecasts, dict) or set(forecasts) != {arm.name for arm in arms}:
            raise _RagExperimentError("answer cell forecasts must cover both frozen arms")
        if any(
            not math.isfinite(float(value)) or float(value) <= 0 for value in forecasts.values()
        ):
            raise _RagExperimentError("answer cell forecasts must be finite and positive")
        if any(float(forecasts[arm]) > float(ceilings[arm]) for arm in forecasts):
            raise _RagExperimentError("answer cell forecasts cannot exceed hard ceilings")

    required_case_fields = {"id", "question", "expected_answer", "role", "permissions"}
    case_ids = [str(case.get("id")) for case in cases]
    if not cases or len(case_ids) != len(set(case_ids)):
        raise _RagExperimentError("cases must be non-empty with unique ids")

    def _freeze_one_case(case: dict[str, Any]) -> dict[str, Any]:
        missing_fields = sorted(required_case_fields - case.keys())
        if missing_fields:
            raise _RagExperimentError(
                f"case {case.get('id')!r} missing frozen fields: {missing_fields}"
            )
        retrieval = retrieve_fn(case)
        if not isinstance(retrieval, dict):
            raise _RagExperimentError(f"retrieval for {case['id']!r} did not return an object")
        if not retrieval.get("contexts"):
            raise _RagExperimentError(f"retrieval for {case['id']!r} returned no contexts")
        if not isinstance(retrieval.get("sources"), list):
            raise _RagExperimentError(f"retrieval for {case['id']!r} omitted source metadata")
        if len(retrieval["contexts"]) != len(retrieval["sources"]):
            raise _RagExperimentError(
                f"retrieval for {case['id']!r} has mismatched contexts and sources"
            )
        if not isinstance(retrieval.get("retrieval_identity"), dict):
            raise _RagExperimentError(f"retrieval for {case['id']!r} omitted retrieval identity")
        context_payload = json.dumps(
            retrieval,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "case": case,
            "retrieval": retrieval,
            "context_hash": f"sha256:{hashlib.sha256(context_payload).hexdigest()}",
        }

    workers = max(1, min(retrieve_max_workers, len(cases)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        frozen_cases = list(pool.map(_freeze_one_case, cases))

    if harness_output_cap_tokens_by_arm is None:
        derived_caps = {
            arm.name: arm.harness_output_cap_tokens
            for arm in arms
            if arm.harness_output_cap_tokens is not None
        }
        if len(derived_caps) != len(arms):
            raise _RagExperimentError(
                "harness_output_cap_tokens_by_arm is required when arms omit "
                "harness_output_cap_tokens"
            )
        resolved_caps = derived_caps
    else:
        resolved_caps = dict(harness_output_cap_tokens_by_arm)
    attested_by_arm = {arm.name: arm.attested_output_cap_tokens for arm in arms}
    resolved_output_ceiling_is_harness_cap = (
        output_ceiling_is_harness_cap
        if output_ceiling_is_harness_cap is not None
        else infer_output_ceiling_is_harness_cap(resolved_caps, attested_by_arm, arms[1].name)
    )

    frozen_arms: list[dict[str, object]] = []
    for arm in arms:
        arm_blob: dict[str, object] = dict(asdict(arm))
        projection = (arm_profile_projections or {}).get(arm.name)
        if projection is not None:
            arm_blob = merge_profile_projection_onto_arm_blob(arm_blob, projection)
        frozen_arms.append(arm_blob)

    document_without_hash: dict[str, Any] = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "created_at": created_at,
        "runtime": asdict(runtime),
        "arms": frozen_arms,
        "policy": {
            "expected_case_ids": case_ids,
            "answer_arms": [arm.name for arm in arms],
            "repeats": 3,
            "metrics": ["factual_correctness", "faithfulness"],
            "max_opportunity_failure_rate": 0.02,
            "regression_tolerance": 0.05,
            "bootstrap_seed": 7,
            "answer_order_seed": int(answer_order_seed),
            "candidate_route_providers": list(candidate_route_providers),
            "route_providers_by_arm": {
                arm_name: list(providers)
                for arm_name, providers in (route_providers_by_arm or {}).items()
            },
            "expected_requested_routes": [
                [arm.name, arm.requested_provider, arm.requested_model] for arm in arms
            ],
            "allowed_actual_models": {
                arm.name: list(
                    (allowed_actual_models or {}).get(
                        arm.name,
                        (
                            DEEPSEEK_ACTUAL_MODELS
                            if arm.requested_model == "deepseek/deepseek-v4-flash-0731"
                            else (arm.requested_model,)
                        ),
                    )
                )
                for arm in arms
            },
            "harness_output_cap_tokens_by_arm": resolved_caps,
            "attested_output_cap_tokens_by_arm": dict(attested_by_arm),
            "output_ceiling_is_harness_cap": resolved_output_ceiling_is_harness_cap,
            "candidate_reasoning_effort": candidate_reasoning_effort,
        },
        "profile_ids": {
            "baseline_profile_id": baseline_profile_id,
            "candidate_profile_id": candidate_profile_id,
        },
        "judge": judge or {"provider": "azure", "model": "gpt-5.6-luna", "version": "v1"},
        "route_policy": route_policy or {},
        "route_policy_by_arm": route_policy_by_arm or {},
        "artifact_identity": artifact_identity or {},
        "environment_evidence": environment_evidence or {},
        "campaign_cap_usd": campaign_cap_usd,
        "spend_stages": spend_stages,
        "cases": frozen_cases,
    }
    freeze_payload = json.dumps(
        document_without_hash,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    freeze_hash = f"sha256:{hashlib.sha256(freeze_payload).hexdigest()}"
    document = {**document_without_hash, "freeze_hash": freeze_hash}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("x", encoding="utf-8") as file_handle:
            json.dump(document, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
            file_handle.write("\n")
    except FileExistsError as exc:
        raise _RagExperimentError(f"frozen experiment already exists: {output_path}") from exc

    return FrozenRagExperiment(
        freeze_hash=freeze_hash,
        output_path=output_path,
        document=document,
    )


# Shared per-answer rules — RagTask delegates here.


def infer_output_ceiling_is_harness_cap(
    harness_output_cap_tokens_by_arm: dict[str, int],
    attested_output_cap_tokens_by_arm: dict[str, int | None],
    candidate_arm_name: str,
) -> bool:
    """True when the candidate arm cap is a harness cost control, not attested provider max."""
    harness_cap = harness_output_cap_tokens_by_arm.get(candidate_arm_name)
    attested_cap = attested_output_cap_tokens_by_arm.get(candidate_arm_name)
    if attested_cap is not None and harness_cap is not None:
        return harness_cap != attested_cap
    return True


def reasoning_tokens_spent(response: dict[str, Any]) -> int | None:
    """Reasoning tokens for one answer, or None when the provider did not report any."""
    for value in (
        response.get("native_tokens_reasoning"),
        (response.get("reasoning_evidence") or {}).get("native_tokens_reasoning")
        if isinstance(response.get("reasoning_evidence"), dict)
        else None,
        _azure_reasoning_tokens(response.get("response_metadata") or {}),
    ):
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return _azure_reasoning_tokens(response.get("response_metadata") or {})


def _azure_reasoning_tokens(response_metadata: dict[str, Any]) -> int | None:
    """Azure/OpenAI reasoning-token count, or None when the key is absent."""
    usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    value = details.get("reasoning_tokens")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_answer_failure(response: dict[str, Any]) -> str | None:
    """Classify one answer as an operational failure, or None if it is usable."""
    if response.get("error"):
        return "provider_error"
    finish_reason = str(response.get("finish_reason") or "").casefold()
    output_tokens = int(response.get("output_tokens") or 0)
    max_output_tokens = int(response.get("max_output_tokens") or 0)
    answer_blank = not str(response.get("answer") or "").strip()
    reasoning_tokens = reasoning_tokens_spent(response)
    length_stop = finish_reason in {"length", "max_tokens"} or (
        max_output_tokens > 0 and output_tokens >= max_output_tokens
    )
    if length_stop:
        if answer_blank and reasoning_tokens is not None and reasoning_tokens > 0:
            return "reasoning_budget_starvation"
        attested = response.get("attested_output_cap_tokens")
        harness = response.get("harness_output_cap_tokens")
        if attested is not None and max_output_tokens == attested:
            return "provider_output_cap"
        if harness is not None and max_output_tokens == harness:
            return "harness_output_cap"
        if max_output_tokens > 0 and 0 < output_tokens < max_output_tokens:
            return "provider_output_cap"
        return "harness_output_cap"
    if answer_blank:
        if (reasoning_tokens is not None and reasoning_tokens > 0) or output_tokens > 0:
            return "reasoning_budget_starvation"
        return "empty_answer"
    return None


def refresh_operational_failure_labels(rows: list[dict[str, Any]]) -> None:
    """Re-run classifier after enrich when native token fields arrive."""
    for row in rows:
        row["operational_failure"] = classify_answer_failure(row)


def _expected_provider_by_arm(policy: RagGatePolicy) -> dict[str, str]:
    return {arm: provider for arm, provider, _model in policy.expected_requested_routes}


def answer_evidence_failures(
    row: dict[str, Any],
    *,
    policy: dict[str, Any],
    spend_stages: dict[str, Any] | None,
    require_route_attestation: bool = True,
) -> list[str]:
    """Validate one paid answer without relying on later judge output."""
    expected_route = next(
        (route for route in policy["expected_requested_routes"] if route[0] == row.get("arm")),
        None,
    )
    if expected_route is None:
        return ["missing_frozen_route"]
    failures: list[str] = []
    if [row.get("arm"), row.get("requested_provider"), row.get("requested_model")] != list(
        expected_route
    ):
        failures.append("requested_route_mismatch")
    if row.get("operational_failure"):
        # Operational failures count toward the opportunity budget; skip provenance checks.
        return failures
    _, expected_provider, _ = expected_route
    pending_route = (
        expected_provider == "openrouter" and row.get("route_metadata_status") == "pending"
    )
    if pending_route and not require_route_attestation:
        pass  # Route metadata settled after the answer matrix.
    elif expected_provider == "openrouter":
        if row.get("route_metadata_status") != "verified":
            failures.append("route_metadata_unverified")
        if row.get("actual_provider") not in set(
            dict(policy.get("route_providers_by_arm") or {}).get(str(row.get("arm")), ())
            or policy.get("candidate_route_providers", ())
        ):
            failures.append("unexpected_actual_provider")
        if row.get("actual_provider_source") != "route_metadata":
            failures.append("actual_provider_unattested")
        if row.get("actual_model_source") != "route_metadata":
            failures.append("actual_model_unattested")
    else:
        if row.get("route_metadata_status") != "not_applicable":
            failures.append("unexpected_route_metadata_status")
        if row.get("actual_provider") != expected_route[1]:
            failures.append("unexpected_actual_provider")
        if row.get("actual_provider_source") not in {
            "provider_response",
            "azure_client_type_endpoint_and_deployment",
        }:
            failures.append("actual_provider_unattested")
        if row.get("actual_model_source") != "provider_response":
            failures.append("actual_model_unattested")
    allowed_models = set(policy.get("allowed_actual_models", {}).get(str(row.get("arm")), ()))
    if not (pending_route and not require_route_attestation) and (
        row.get("actual_model") in {None, "", "unknown", "unavailable"}
        or row.get("actual_model") not in allowed_models
    ):
        failures.append("unexpected_actual_model")
    if row.get("request_id_source") != "provider" or row.get("request_id") in {
        None,
        "",
        "unknown",
        "unavailable",
    }:
        failures.append("missing_provider_request_id")
    if int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0) <= 0:
        failures.append("missing_usage")
    if row.get("finish_reason") in {None, "", "unknown", "error"}:
        failures.append("missing_finish_reason")
    try:
        latency_ms = float(row.get("latency_ms"))
        cost_usd = float(row.get("cost_usd"))
    except (TypeError, ValueError):
        failures.append("invalid_numeric_evidence")
    else:
        if not math.isfinite(latency_ms) or latency_ms < 0:
            failures.append("invalid_latency")
        if not math.isfinite(cost_usd) or cost_usd < 0:
            failures.append("invalid_cost")
        if spend_stages:
            ceiling = float(spend_stages["answer_cell_ceiling_usd"][str(row.get("arm"))])
            if cost_usd > ceiling + BUDGET_EPSILON_USD:
                failures.append("answer_cell_ceiling_exceeded")
    return failures


def enforce_operational_budget(rows: list[dict[str, Any]], *, policy: dict[str, Any]) -> None:
    """Stop a run already doomed to HOLD before it spends on more answers or the judge."""
    operational = sum(1 for row in rows if row.get("operational_failure"))
    if not operational:
        return
    expected_rows = (
        len(policy["expected_case_ids"]) * len(policy["answer_arms"]) * int(policy["repeats"])
    )
    max_rate = float(policy["max_opportunity_failure_rate"])
    if expected_rows and operational / expected_rows > max_rate:
        raise _RagExperimentError(
            f"operational failure budget exhausted: {operational} operational answer "
            f"failures already exceed the {max_rate:.0%} answer-opportunity budget - "
            "stopping before further spend"
        )


def verify_frozen_contract(frozen: FrozenRagExperiment) -> None:
    """Re-read the frozen contract from disk and re-derive its hash."""
    _verify_frozen_contract(frozen, error_type=_RagExperimentError)
