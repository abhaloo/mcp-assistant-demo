"""Live RAG freeze assembly: budget projection, runtime identity, freeze contract."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.eval.dataset import DATASET_PATH, load_eval_cases
from app.experiments.arm_profiles import (
    CampaignConfig,
    build_pairwise_campaign,
    campaign_uses_openrouter,
)
from app.experiments.azure_attestation import (
    verify_frozen_azure_deployments,
    verify_live_azure_deployment,
)
from app.experiments.core.money import (
    estimate_cost_usd,
    round_reservation_usd,
)
from app.experiments.core.runtime_identity import RuntimeIdentity
from app.experiments.core.work_matrix import (
    DEFAULT_MAX_WORKERS,
    EFFORT_PROBE_MAX_WORKERS,
    ProviderConcurrencyLimits,
)
from app.experiments.effort_probe import VERDICT_INERT
from app.experiments.live_errors import LiveRagEvalError
from app.experiments.manifest import (
    EXPERIMENT_ID,
    MANIFEST_PATH,
    RAG_CASE_IDS_PATH,
    load_manifest,
    sha256_file,
    verify_manifest,
)
from app.experiments.rag_answer_path import (
    ANSWER_INPUT_CEILING_TOKENS,
    EFFORT_PROBE_RESERVE_USD,
    EXPERIMENT_RETRIEVAL_TOP_K,
    effort_probe_controls,
    rendered_prompt_for_hash,
    require_effort_separated,
    retrieve_case_snapshot,
    run_effort_probe,
    validated_openrouter_base_url,
)
from app.experiments.rag_experiment import (
    FREEZE_RETRIEVAL_MAX_WORKERS,
    AnswerArm,
    FrozenRagExperiment,
    freeze_rag_experiment,
    infer_output_ceiling_is_harness_cap,
)
from app.experiments.rag_gold_answerability import validate_frozen_gold_answerability
from app.paths import REPO_ROOT
from app.pricing.prices import PRICES_PATH
from app.providers.openrouter_controls import OpenRouterControls
from app.providers.reasoning_effort_policy import default_effort

_logger = logging.getLogger(__name__)

REPEATS = 3
REPEATABILITY_CASES = 15
CANARY_SAMPLES = 6
JUDGE_DEPLOYMENT = "gpt-5.6-luna"
EST_JUDGE_PER_SAMPLE_USD = 0.010
CANARY_RESERVE_USD = 0.15
ANSWER_FORECAST_SAFETY_FACTOR = 1.02
EFFORT_PROBE_WAIVER_ENV = "DEEPSEEK_EFFORT_PROBE_WAIVER"


def _harness_output_cap_tokens_by_arm(config: CampaignConfig) -> dict[str, int]:
    return {
        config.baseline.arm_name: config.baseline.harness_output_cap_tokens,
        config.candidate.arm_name: config.candidate.harness_output_cap_tokens,
    }


def _est_answer_usd_by_arm(config: CampaignConfig) -> dict[str, float]:
    return {
        config.baseline.arm_name: config.baseline.est_answer_usd,
        config.candidate.arm_name: config.candidate.est_answer_usd,
    }


def controls_from_route_policy(route_policy: dict[str, Any]) -> OpenRouterControls | None:
    """Rehydrate frozen route policy into fail-closed OpenRouter controls."""
    if not route_policy:
        return None
    return OpenRouterControls(**route_policy)


def controls_for_frozen_arm(
    *, arm_name: str, document: dict[str, Any]
) -> OpenRouterControls | None:
    """Per-arm OpenRouter controls from a frozen experiment, with legacy single-policy fallback."""
    by_arm = document.get("route_policy_by_arm") or {}
    arm_policy = by_arm.get(arm_name)
    if arm_policy:
        return OpenRouterControls(**arm_policy)
    legacy = document.get("route_policy") or {}
    if legacy:
        return controls_from_route_policy(legacy)
    return None


def load_frozen_rag_cases() -> list[dict[str, Any]]:
    verify_manifest(load_manifest())
    case_ids = json.loads(RAG_CASE_IDS_PATH.read_text(encoding="utf-8"))
    cases = load_eval_cases("semantic", subset=case_ids)
    if [case["id"] for case in cases] != case_ids:
        by_id = {case["id"]: case for case in cases}
        try:
            cases = [by_id[case_id] for case_id in case_ids]
        except KeyError as exc:
            raise LiveRagEvalError(f"frozen case missing: {exc.args[0]}") from exc
    if len(cases) != len(case_ids):
        raise LiveRagEvalError(
            f"manifest subset mismatch: expected {len(case_ids)} cases, got {len(cases)}"
        )
    return cases


def select_repeatability_subset(case_ids: list[str], *, size: int) -> tuple[str, ...]:
    if size < 1 or size > len(set(case_ids)):
        raise ValueError("repeatability subset size is outside the unique case set")
    ordered = sorted(
        set(case_ids),
        key=lambda case_id: hashlib.sha256(case_id.encode("utf-8")).hexdigest(),
    )
    return tuple(ordered[:size])


def project_budget_usd(*, campaign: CampaignConfig, n_cases: int) -> dict[str, Any]:
    answer_arms = campaign.answer_arms
    harness_caps = _harness_output_cap_tokens_by_arm(campaign)
    est_answer_usd = _est_answer_usd_by_arm(campaign)
    answer_calls = n_cases * len(answer_arms) * REPEATS
    answer_usd = n_cases * REPEATS * sum(est_answer_usd.values())
    primary_judge_samples = answer_calls
    repeatability_judge_samples = REPEATABILITY_CASES * len(answer_arms) * REPEATS
    judge_samples = primary_judge_samples + repeatability_judge_samples + CANARY_SAMPLES
    judge_usd = judge_samples * EST_JUDGE_PER_SAMPLE_USD
    total_usd = answer_usd + judge_usd
    answer_cell_ceilings = {
        arm_name: estimate_cost_usd(
            arm.requested_model,
            ANSWER_INPUT_CEILING_TOKENS,
            harness_caps[arm_name],
        )
        for arm_name, arm in answer_arms.items()
    }
    from app.experiments.live_rag_judge import JUDGE_FORECAST_SAFETY_FACTOR

    answer_reserve_usd = round_reservation_usd(answer_usd * ANSWER_FORECAST_SAFETY_FACTOR)
    return {
        "answer_calls": answer_calls,
        "answer_usd": round(answer_usd, 4),
        "primary_judge_samples": primary_judge_samples,
        "repeatability_judge_samples": repeatability_judge_samples,
        "canary_judge_samples": CANARY_SAMPLES,
        "judge_usd": round(judge_usd, 4),
        "total_usd": round(total_usd, 4),
        "campaign_cap_usd": campaign.campaign_cap_usd,
        "answer_input_ceiling_tokens": ANSWER_INPUT_CEILING_TOKENS,
        "harness_output_cap_tokens_by_arm": dict(harness_caps),
        "answer_cell_ceiling_usd": {
            arm: round(value, 9) for arm, value in answer_cell_ceilings.items()
        },
        "answer_cell_forecast_usd": dict(est_answer_usd),
        "answer_forecast_safety_factor": ANSWER_FORECAST_SAFETY_FACTOR,
        "answer_reserve_usd": answer_reserve_usd,
        "canary_reserve_usd": CANARY_RESERVE_USD,
        "judge_reserve_usd": round_reservation_usd(
            (primary_judge_samples + repeatability_judge_samples)
            * EST_JUDGE_PER_SAMPLE_USD
            * JUDGE_FORECAST_SAFETY_FACTOR
        ),
    }


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise LiveRagEvalError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _dependency_hash(repo_root: Path) -> str:
    hasher = hashlib.sha256()
    paths = [repo_root / "pyproject.toml", repo_root / "uv.lock", repo_root / "requirements.txt"]
    existing = [path for path in paths if path.is_file()]
    if not existing:
        raise LiveRagEvalError("no dependency lock inputs found")
    for path in existing:
        hasher.update(path.name.encode("utf-8"))
        hasher.update(path.read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def _safe_environment_hash(*, campaign: CampaignConfig | None = None) -> str:
    safe_environment = _safe_environment_snapshot(campaign=campaign)
    payload = json.dumps(
        safe_environment,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _safe_environment_snapshot(*, campaign: CampaignConfig | None = None) -> dict[str, Any]:
    package_versions: dict[str, str] = {}
    for package in ("ragas", "instructor", "pydantic", "langchain", "langchain-openai", "openai"):
        try:
            package_versions[package] = version(package)
        except PackageNotFoundError:
            package_versions[package] = "not-installed"
    redaction_policy = {
        "enabled": settings.redaction_enabled,
        "entities": list(settings.redaction_entities),
        "confidence_threshold": settings.redaction_confidence_threshold,
        "analyzer_sha256": f"sha256:{sha256_file(REPO_ROOT / 'app/guardrails/analyzer.py')}",
        "document_redaction_sha256": (
            f"sha256:{sha256_file(REPO_ROOT / 'app/guardrails/document_redaction.py')}"
        ),
    }
    snapshot: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "packages": package_versions,
        "app_environment": settings.environment,
        "retriever_kind": settings.retriever_kind,
        "azure_search_host": urlparse(settings.azure_search_endpoint or "").hostname,
        "azure_search_index": settings.azure_search_index,
        "azure_search_query_mode": settings.azure_search_query_mode,
        "production_top_k": settings.top_k,
        "experiment_top_k": EXPERIMENT_RETRIEVAL_TOP_K,
        "rerank_enabled": settings.rerank_enabled,
        "redaction_policy": redaction_policy,
        "chat_provider": settings.chat_provider,
        "judge_azure_endpoint_host": urlparse(settings.azure_endpoint or "").hostname,
        "judge_azure_api_version": settings.azure_api_version,
        "openrouter_base_url": validated_openrouter_base_url(),
        "judge_model": JUDGE_DEPLOYMENT,
        "answer_matrix_concurrency": {
            "total": DEFAULT_MAX_WORKERS,
            "azure": ProviderConcurrencyLimits().azure,
            "openrouter": ProviderConcurrencyLimits().openrouter,
        },
        "effort_probe_max_workers": EFFORT_PROBE_MAX_WORKERS,
    }
    if campaign is not None:
        snapshot["base_model"] = campaign.baseline.requested_model
        snapshot["candidate_model"] = campaign.candidate.requested_model
        snapshot["effective_reasoning_effort_by_arm"] = {
            arm.name: arm.reasoning_effort or default_effort(arm.requested_model)
            for arm in campaign.answer_arms.values()
        }
    return snapshot


def current_runtime_identity(
    repo_root: Path = REPO_ROOT,
    *,
    campaign: CampaignConfig | None = None,
) -> RuntimeIdentity:
    """Require clean code that exactly matches the configured upstream branch."""
    commit_sha = _git(repo_root, "rev-parse", "HEAD")
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=all")
    upstream_sha = _git(repo_root, "rev-parse", "@{u}")
    if commit_sha != upstream_sha:
        raise LiveRagEvalError(
            f"runtime commit is not pushed exactly: HEAD={commit_sha}, upstream={upstream_sha}"
        )
    return RuntimeIdentity(
        commit_sha=commit_sha,
        dirty=bool(status),
        environment_hash=_safe_environment_hash(campaign=campaign),
        dependency_lock_hash=_dependency_hash(repo_root),
    )


def _campaign_for_runtime_check(frozen: FrozenRagExperiment) -> CampaignConfig:
    profile_ids = frozen.document.get("profile_ids") or {}
    baseline_id = profile_ids.get("baseline_profile_id") or frozen.document.get(
        "baseline_profile_id"
    )
    candidate_id = profile_ids.get("candidate_profile_id") or frozen.document.get(
        "candidate_profile_id"
    )
    if not baseline_id or not candidate_id:
        raise LiveRagEvalError("frozen contract missing baseline_profile_id / candidate_profile_id")
    route_effort = frozen.document.get("candidate_reasoning_effort")
    return build_pairwise_campaign(
        baseline_id,
        candidate_id,
        route_effort=route_effort,
    )


def verify_live_runtime_invariants(
    frozen: FrozenRagExperiment,
    *,
    stage: str,
) -> None:
    """Bind every paid stage and verdict to the frozen code, environment, and deployments."""
    current = current_runtime_identity(campaign=_campaign_for_runtime_check(frozen))
    expected = frozen.document.get("runtime")
    observed = asdict(current)
    if observed != expected:
        drifted_fields = sorted(
            key
            for key in set(observed) | set(expected or {})
            if observed.get(key) != (expected or {}).get(key)
        )
        raise LiveRagEvalError(
            f"runtime identity drifted during {stage}: {', '.join(drifted_fields)}"
        )
    verify_frozen_azure_deployments(frozen, stage=stage)


def _retrieval_identity() -> dict[str, Any]:
    return {
        "backend": settings.retriever_kind,
        "host": urlparse(settings.azure_search_endpoint or "").hostname,
        "index": (
            settings.azure_search_index
            if settings.retriever_kind == "azure_search"
            else settings.collection_name
        ),
        "query_mode": settings.azure_search_query_mode,
        "top_k": EXPERIMENT_RETRIEVAL_TOP_K,
        "rerank_enabled": settings.rerank_enabled,
    }


def _validate_freeze_preconditions(runtime: RuntimeIdentity) -> None:
    """Refuse to freeze against a dirty checkout, the wrong judge provider, or a loose
    redaction policy -- every check that must run before anything is retrieved or spent."""
    if runtime.dirty:
        raise LiveRagEvalError("dirty runtime: commit and push before retrieval freeze")
    if settings.chat_provider != "azure":
        raise LiveRagEvalError("campaign judge requires chat_provider=azure")
    if not settings.redaction_enabled or settings.redaction_entities != ["PHONE_NUMBER"]:
        raise LiveRagEvalError("campaign requires the frozen PHONE_NUMBER redaction policy")


_REQUIRED_CASE_FIELDS = frozenset({"id", "question", "expected_answer", "role", "permissions"})


def _validate_case_shape(cases: list[dict[str, Any]]) -> None:
    """Refuse a malformed case list before anything is retrieved or spent.

    freeze_rag_experiment enforces this same shape too, but only once it finally
    runs -- and in this flow that is LAST, after retrieval and the paid effort
    probe. Without this gate, a duplicate id or a case missing a required field
    would burn real retrieval and effort-probe spend before ever being rejected.
    """
    case_ids = [str(case.get("id")) for case in cases]
    if not cases or len(case_ids) != len(set(case_ids)):
        raise LiveRagEvalError("cases must be non-empty with unique ids")
    for case in cases:
        missing_fields = sorted(_REQUIRED_CASE_FIELDS - case.keys())
        if missing_fields:
            raise LiveRagEvalError(
                f"case {case.get('id')!r} missing frozen fields: {missing_fields}"
            )


def _verify_openrouter_capabilities(campaign: CampaignConfig) -> None:
    """Refuse every OpenRouter arm whose capability probe is not VERIFIED (or waived)."""
    validated_openrouter_base_url()
    from app.providers.openrouter_capability_probe import (
        STATUS_HOLD,
        STATUS_VERIFIED,
        probe_candidate_capabilities,
    )

    for arm in campaign.answer_arms.values():
        if arm.requested_provider != "openrouter":
            continue
        probe = probe_candidate_capabilities(
            base_url=validated_openrouter_base_url(),
            slug=arm.requested_model,
            expected_reasoning_effort=arm.reasoning_effort,
        )
        if probe.status == STATUS_HOLD:
            raise LiveRagEvalError(f"OpenRouter capability probe HOLD: {probe.detail}")
        if probe.status != STATUS_VERIFIED:
            # An operator waiver permits proceeding when the probe is unverified; the
            # probe result still lands in evidence, so the waiver is auditable.
            waiver = os.environ.get("OPENROUTER_CAPABILITY_PROBE_WAIVER") == "1" and getattr(
                probe, "waivable", True
            )
            if not waiver:
                raise LiveRagEvalError(
                    "OpenRouter capability probe UNVERIFIED (fail-closed): "
                    f"{probe.detail}. Set OPENROUTER_CAPABILITY_PROBE_WAIVER=1 to waive "
                    "only unreachable/absent-slug probes."
                )


def _prefetch_case_retrievals(
    cases: list[dict[str, Any]],
    *,
    retrieval_identity: dict[str, Any],
    max_workers: int = FREEZE_RETRIEVAL_MAX_WORKERS,
) -> dict[str, dict[str, Any]]:
    """Retrieve every case exactly once, concurrently, before the single freeze call.

    freeze_rag_experiment retrieves internally by default, but the rendered-prompt
    hash and the effort probe both need the retrieved content too -- and they must run
    BEFORE the document is written, not after. Retrieving here, once, and handing
    freeze_rag_experiment a cache lookup instead of a live retrieve_fn is what makes a
    single freeze -- not freeze, mutate, re-hash -- possible.
    """
    if not cases:
        return {}

    def _fetch(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        retrieval = retrieve_case_snapshot(case, retrieval_identity=retrieval_identity)
        return case["id"], {"case": case, "retrieval": retrieval}

    workers = max(1, min(max_workers, len(cases)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        fetched = list(pool.map(_fetch, cases))
    return dict(fetched)


def _rendered_prompt_evidence(
    answer_arms: dict[str, AnswerArm],
    *,
    cases: list[dict[str, Any]],
    prefetched: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Per-arm rendered-prompt hashes and the (arm-equal) request timeout.

    Folded into environment_evidence rather than policy: freeze_rag_experiment's
    policy dict is built entirely from its own named parameters, with no field for
    late-computed evidence like this. environment_evidence already is that seam --
    zero readers depend on this evidence's exact placement (re-verified before this
    task).
    """
    if not cases:
        return {}
    first_case = prefetched[cases[0]["id"]]
    rendered_hashes = {
        arm.name: hashlib.sha256(
            rendered_prompt_for_hash(arm, first_case).encode("utf-8")
        ).hexdigest()
        for arm in answer_arms.values()
    }
    return {
        "rendered_prompt_sha256_by_arm": rendered_hashes,
        "request_timeout_s": next(iter(answer_arms.values())).request_timeout_s,
    }


def _effort_probe_evidence(
    campaign: CampaignConfig,
    *,
    cases: list[dict[str, Any]],
    prefetched: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Run each arm's effort probe against the pre-fetched retrieval, before freeze.

    Collected here so the single freeze_rag_experiment call already carries this
    evidence -- no document is hashed, written, mutated, and re-hashed.
    """
    if not cases:
        return {}
    frozen_cases = [prefetched[case["id"]] for case in cases]
    effort_probe: dict[str, Any] = {}
    operator_waivers: list[dict[str, Any]] = []
    for arm in campaign.answer_arms.values():
        if arm.reasoning_effort is None:
            continue
        low_effort = "low"
        high_effort = arm.reasoning_effort
        if low_effort == high_effort:
            effort_probe[arm.name] = "skipped: arm already runs at low"
            continue
        base_controls = campaign.route_controls_by_arm.get(arm.name)
        if arm.requested_provider == "openrouter" and base_controls is None:
            raise LiveRagEvalError(
                f"no OpenRouter controls configured for effort probe on arm {arm.name!r}"
            )
        probe = run_effort_probe(
            frozen_cases,
            arm=arm,
            low_effort=low_effort,
            high_effort=high_effort,
            reserve_usd=EFFORT_PROBE_RESERVE_USD,
            low_controls=effort_probe_controls(base_controls, effort=low_effort),
            high_controls=effort_probe_controls(base_controls, effort=high_effort),
        )
        probe_record = asdict(probe)
        if not probe.passed:
            # An operator waiver records the probe verdict as evidence and proceeds; the
            # verdict still lands in the frozen document, so the waiver is auditable.
            inert_waiver = (
                os.environ.get(EFFORT_PROBE_WAIVER_ENV) == "1" and probe.verdict == VERDICT_INERT
            )
            if not inert_waiver:
                require_effort_separated(probe, model=arm.requested_model)
            else:
                _logger.warning(
                    "[live] operator waived %s effort probe for %r (%s): %s. "
                    "Set only when you accept an inert effort knob.",
                    probe.verdict,
                    arm.requested_model,
                    arm.name,
                    probe.detail,
                )
                probe_record["operator_waived"] = True
                probe_record["waiver_env"] = EFFORT_PROBE_WAIVER_ENV
                operator_waivers.append(
                    {
                        "kind": "effort_probe_inert",
                        "arm": arm.name,
                        "model": arm.requested_model,
                        "verdict": probe.verdict,
                        "ratio": probe.ratio,
                        "detail": probe.detail,
                        "waiver_env": EFFORT_PROBE_WAIVER_ENV,
                    }
                )
        effort_probe[arm.name] = probe_record
    evidence: dict[str, Any] = {"effort_probe": effort_probe}
    if operator_waivers:
        evidence["operator_waivers"] = operator_waivers
    return evidence


def freeze_live_rag_experiment(
    *,
    campaign: CampaignConfig,
    output_path: Path,
    runtime: RuntimeIdentity | None = None,
) -> FrozenRagExperiment:
    """Freeze complete cases, retrievals, routes, judge policy, and runtime identity.

    All evidence -- retrieval, rendered-prompt hashes, the effort probe, the
    OpenRouter capability probe -- is collected before the single
    freeze_rag_experiment() call that writes the document once. No document is
    hashed, written, mutated, and re-hashed.
    """
    resolved_runtime = runtime or current_runtime_identity(campaign=campaign)
    _validate_freeze_preconditions(resolved_runtime)

    answer_arms = campaign.answer_arms
    base_arm = campaign.baseline.arm_name
    candidate_arm = campaign.candidate.arm_name
    harness_caps = _harness_output_cap_tokens_by_arm(campaign)
    uses_openrouter = campaign_uses_openrouter(campaign)
    if uses_openrouter:
        _verify_openrouter_capabilities(campaign)

    cases = load_frozen_rag_cases()
    _validate_case_shape(cases)
    case_ids = [case["id"] for case in cases]
    projection = project_budget_usd(campaign=campaign, n_cases=len(cases))
    if float(projection["total_usd"]) > campaign.campaign_cap_usd:
        raise LiveRagEvalError(
            f"projected repair campaign exceeds the frozen ${campaign.campaign_cap_usd:g} cap"
        )

    retrieval_identity = _retrieval_identity()
    prefetched = _prefetch_case_retrievals(cases, retrieval_identity=retrieval_identity)

    route_policy_by_arm = {
        arm_name: asdict(controls) for arm_name, controls in campaign.route_controls_by_arm.items()
    }
    route_providers_by_arm = {
        arm_name: list(controls.only or controls.order)
        for arm_name, controls in campaign.route_controls_by_arm.items()
    }
    candidate_controls = campaign.route_controls_by_arm.get(candidate_arm)
    attested_by_arm = {arm.name: arm.attested_output_cap_tokens for arm in answer_arms.values()}

    environment_evidence = _safe_environment_snapshot(campaign=campaign)
    environment_payload = json.dumps(
        environment_evidence,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if (
        f"sha256:{hashlib.sha256(environment_payload).hexdigest()}"
        != resolved_runtime.environment_hash
    ):
        raise LiveRagEvalError("runtime environment changed between identity and freeze")

    # Every piece of live evidence lands in environment_evidence before the freeze
    # call -- this dict, not a post-freeze mutation, is what the frozen document
    # ends up hashing.
    environment_evidence.update(
        _rendered_prompt_evidence(dict(answer_arms), cases=cases, prefetched=prefetched)
    )
    environment_evidence.update(
        _effort_probe_evidence(campaign, cases=cases, prefetched=prefetched)
    )

    from app.experiments.live_rag_judge import judge_version

    judge_identity = judge_version()
    judge_identity["deployment_attestation"] = verify_live_azure_deployment(
        judge_identity["deployment_attestation"]
    )
    answer_deployment_attestations = {
        campaign.baseline.arm_name: dict(campaign.baseline.deployment_attestation),
        campaign.candidate.arm_name: dict(campaign.candidate.deployment_attestation),
    }
    for arm_name, attestation in list(answer_deployment_attestations.items()):
        if answer_arms[arm_name].requested_provider == "azure":
            answer_deployment_attestations[arm_name] = verify_live_azure_deployment(attestation)
    judge_config = {
        "provider": "azure",
        "model": JUDGE_DEPLOYMENT,
        "version": judge_identity,
        "repeatability_case_ids": list(
            select_repeatability_subset(case_ids, size=REPEATABILITY_CASES)
        ),
        "sample_order_seed": 137,
        "projection": projection,
    }
    arm_profile_projections = {
        base_arm: campaign.baseline.freeze_projection(),
        candidate_arm: campaign.candidate.freeze_projection(),
    }

    frozen = freeze_rag_experiment(
        experiment_id=f"{EXPERIMENT_ID}-repair-v2",
        cases=cases,
        retrieve_fn=lambda case: prefetched[case["id"]]["retrieval"],
        runtime=resolved_runtime,
        output_path=output_path,
        created_at=datetime.now(UTC).isoformat(),
        arms=tuple(answer_arms.values()),
        judge=judge_config,
        route_policy=route_policy_by_arm.get(candidate_arm, {}) if uses_openrouter else {},
        route_policy_by_arm=route_policy_by_arm if uses_openrouter else {},
        route_providers_by_arm={
            arm_name: tuple(providers) for arm_name, providers in route_providers_by_arm.items()
        }
        if uses_openrouter
        else {},
        artifact_identity={
            "manifest_sha256": f"sha256:{sha256_file(MANIFEST_PATH)}",
            "rag_case_ids_file_sha256": f"sha256:{sha256_file(RAG_CASE_IDS_PATH)}",
            "eval_dataset_sha256": f"sha256:{sha256_file(DATASET_PATH)}",
            "prices_sha256": f"sha256:{sha256_file(PRICES_PATH)}",
            "pyproject_sha256": f"sha256:{sha256_file(REPO_ROOT / 'pyproject.toml')}",
            "uv_lock_sha256": f"sha256:{sha256_file(REPO_ROOT / 'uv.lock')}",
            "requirements_sha256": (f"sha256:{sha256_file(REPO_ROOT / 'requirements.txt')}"),
            "answer_deployment_attestations": answer_deployment_attestations,
        },
        environment_evidence=environment_evidence,
        candidate_route_providers=(
            tuple(
                route_providers_by_arm.get(
                    candidate_arm,
                    candidate_controls.only or candidate_controls.order
                    if candidate_controls
                    else [],
                )
            )
            if uses_openrouter and candidate_controls is not None
            else ()
        ),
        allowed_actual_models={
            base_arm: campaign.baseline.allowed_actual_models,
            candidate_arm: campaign.candidate.allowed_actual_models,
        },
        harness_output_cap_tokens_by_arm=dict(harness_caps),
        candidate_reasoning_effort=(
            candidate_controls.reasoning_effort
            if uses_openrouter and candidate_controls is not None
            else answer_arms[candidate_arm].reasoning_effort
        ),
        output_ceiling_is_harness_cap=infer_output_ceiling_is_harness_cap(
            dict(harness_caps),
            attested_by_arm,
            candidate_arm,
        ),
        baseline_profile_id=campaign.baseline.id,
        candidate_profile_id=campaign.candidate.id,
        arm_profile_projections=arm_profile_projections,
        campaign_cap_usd=campaign.campaign_cap_usd,
        answer_order_seed=99173,
        spend_stages={
            "canary_reserve_usd": projection["canary_reserve_usd"],
            "answer_reserve_usd": projection["answer_reserve_usd"],
            "answer_cell_ceiling_usd": projection["answer_cell_ceiling_usd"],
            "answer_cell_forecast_usd": projection["answer_cell_forecast_usd"],
            "judge_reserve_usd": projection["judge_reserve_usd"],
        },
    )

    campaign_arms = tuple(sorted(arm["name"] for arm in frozen.document["arms"]))
    judge_arms = tuple(sorted(frozen.document["policy"]["answer_arms"]))
    if campaign_arms != judge_arms:
        raise LiveRagEvalError(
            f"judge validator arms {judge_arms} != campaign arms {campaign_arms} -- "
            "the run would HOLD on judge_repeatability_failed after full spend"
        )

    validate_frozen_gold_answerability(frozen.document["cases"])
    return frozen
