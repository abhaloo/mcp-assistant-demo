"""Declarative eval arm profiles loaded from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from app.config import settings
from app.experiments.rag_experiment import AnswerArm
from app.experiments.sql_freeze import SqlArm
from app.paths import REPO_ROOT
from app.providers.azure_reasoning import needs_azure_reasoning_completion_args
from app.providers.model_registry import resolve_openrouter_spec
from app.providers.openrouter_controls import (
    EVAL_DEFAULT_ORDER,
    OpenRouterControls,
    assert_openrouter_controls,
    default_eval_controls,
)
from app.providers.reasoning_effort_policy import (
    UnsupportedReasoningEffortError,
    assert_effort_supported,
    default_effort,
    supported_efforts,
)

_VALID_VERBOSITY = frozenset({"low", "medium", "high"})
_DEFAULT_PROFILES_DIR = REPO_ROOT / "evals" / "experiments" / "arm_profiles"
_EXTENDS_KEY = "extends"
_SQL_TIMEOUT_KEY = "sql_request_timeout_s"


class ArmProfileError(ValueError):
    """Invalid arm profile registry data."""


@dataclass(frozen=True)
class ArmProfile:
    id: str
    arm_name: str
    provider: Literal["azure", "openrouter", "deepseek_direct", "openai"]
    requested_model: str
    allowed_actual_models: tuple[str, ...]
    harness_output_cap_tokens: int
    attested_output_cap_tokens: int | None
    reasoning_effort: str | None
    verbosity: str | None
    request_timeout_s: float | None
    provider_order: list[str]
    deployment_attestation: dict[str, object]
    est_answer_usd: float
    chat_deployment: str | None = None
    api_class: str | None = None
    sql_request_timeout_s: float | None = None
    has_sql_request_timeout: bool = False

    def freeze_projection(self) -> dict[str, object]:
        """Single enumeration of profile fields that must land in a freeze contract."""
        return {
            "id": self.id,
            "arm_name": self.arm_name,
            "provider": self.provider,
            "requested_model": self.requested_model,
            "allowed_actual_models": list(self.allowed_actual_models),
            "harness_output_cap_tokens": self.harness_output_cap_tokens,
            "attested_output_cap_tokens": self.attested_output_cap_tokens,
            "reasoning_effort": self.reasoning_effort,
            "verbosity": self.verbosity,
            "request_timeout_s": self.request_timeout_s,
            "provider_order": list(self.provider_order),
            "deployment_attestation": dict(self.deployment_attestation),
            "est_answer_usd": self.est_answer_usd,
            "chat_deployment": self.chat_deployment,
            "api_class": self.api_class,
        }


class AzureDeploymentAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["azure_resource_manager"]
    observed_at: str
    resource_group: str
    account: str
    deployment: str
    model_name: str
    model_version: str
    deployment_state: str
    provisioning_state: str
    sku: str
    allowed_response_models: list[str]


class OpenRouterDeploymentAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["openrouter_generation_metadata"]
    model: str
    allowed_response_models: list[str]


class DeepSeekDirectDeploymentAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["deepseek_direct_response_model"]
    model: str
    allowed_response_models: list[str]


class OpenAIDeploymentAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["openai_response_model"]
    model: str
    allowed_response_models: list[str]


DeploymentAttestation = Annotated[
    AzureDeploymentAttestation
    | OpenRouterDeploymentAttestation
    | DeepSeekDirectDeploymentAttestation
    | OpenAIDeploymentAttestation,
    Field(discriminator="source"),
]


class ArmProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    arm_name: str = Field(min_length=1)
    provider: Literal["azure", "openrouter", "deepseek_direct", "openai"]
    requested_model: str = Field(min_length=1)
    allowed_actual_models: list[str] = Field(default_factory=list)
    harness_output_cap_tokens: int
    attested_output_cap_tokens: int | None = None
    reasoning_effort: str | None = None
    verbosity: Literal["low", "medium", "high"] | None = None
    request_timeout_s: float | None = None
    sql_request_timeout_s: float | None = None
    provider_order: list[str] | None = None
    deployment_attestation: DeploymentAttestation
    est_answer_usd: float
    chat_deployment: str | None = Field(default=None, min_length=1)
    api_class: (
        Literal["responses", "chat_completions", "openrouter_chat", "deepseek_chat"] | None
    ) = None

    @field_validator("reasoning_effort")
    @classmethod
    def _validate_effort(cls, val: str | None, info: ValidationInfo) -> str | None:
        if val is None:
            return None
        model = info.data.get("requested_model")
        provider = info.data.get("provider")
        if model and provider:
            try:
                assert_effort_supported(model, val, provider=provider)
            except UnsupportedReasoningEffortError as exc:
                raise ValueError(str(exc)) from exc
        return val


_DEFAULT_CAMPAIGN_CAPS_PATH = REPO_ROOT / "evals" / "experiments" / "campaign_caps.yaml"
_KNOWN_PROFILE_KEYS = frozenset(ArmProfileModel.model_fields) | {_EXTENDS_KEY}


@dataclass(frozen=True)
class SqlCampaignConfig:
    baseline_id: str
    candidate_id: str
    arms: tuple[SqlArm, ...]
    router_policy: dict[str, object]
    baseline_arm: str
    candidate_arm: str
    campaign_cap_usd: float
    allowed_actual_models: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignConfig:
    baseline: ArmProfile
    candidate: ArmProfile
    campaign_cap_usd: float
    answer_arms: dict[str, AnswerArm]
    route_controls_by_arm: dict[str, OpenRouterControls] = field(default_factory=dict)


@dataclass(frozen=True)
class CampaignCaps:
    campaign_cap_usd: float
    sql_campaign_cap_usd: float | None = None


def load_campaign_caps(path: Path | None = None) -> dict[tuple[str, str], CampaignCaps]:
    """Load explicit baseline_id + candidate_id spend caps from YAML."""
    caps_path = path if path is not None else _DEFAULT_CAMPAIGN_CAPS_PATH
    if not caps_path.is_file():
        raise ArmProfileError(f"campaign caps file not found: {caps_path}")
    raw = yaml.safe_load(caps_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ArmProfileError(f"{caps_path}: campaign caps must be a mapping")
    pairs = raw.get("pairs")
    if not isinstance(pairs, list):
        raise ArmProfileError(f"{caps_path}: pairs must be a list")
    caps: dict[tuple[str, str], CampaignCaps] = {}
    for index, entry in enumerate(pairs):
        if not isinstance(entry, dict):
            raise ArmProfileError(f"{caps_path}: pairs[{index}] must be a mapping")
        baseline_id = entry.get("baseline_id")
        candidate_id = entry.get("candidate_id")
        cap = entry.get("campaign_cap_usd")
        sql_cap = entry.get("sql_campaign_cap_usd")
        if not isinstance(baseline_id, str) or not isinstance(candidate_id, str):
            raise ArmProfileError(
                f"{caps_path}: pairs[{index}] baseline_id and candidate_id must be strings"
            )
        if not isinstance(cap, (int, float)):
            raise ArmProfileError(f"{caps_path}: pairs[{index}] campaign_cap_usd must be a number")
        if sql_cap is not None and not isinstance(sql_cap, (int, float)):
            raise ArmProfileError(
                f"{caps_path}: pairs[{index}] sql_campaign_cap_usd must be a number"
            )
        key = (baseline_id, candidate_id)
        if key in caps:
            raise ArmProfileError(f"{caps_path}: duplicate campaign cap pair {key!r}")
        caps[key] = CampaignCaps(
            campaign_cap_usd=float(cap),
            sql_campaign_cap_usd=float(sql_cap) if sql_cap is not None else None,
        )
    return caps


def lookup_campaign_cap_usd(
    *,
    baseline_id: str,
    candidate_id: str,
    caps: dict[tuple[str, str], CampaignCaps | float] | None = None,
) -> float:
    """Resolve RAG campaign cap for a profile pair; unlisted pairs fail closed."""
    registry = caps if caps is not None else load_campaign_caps()
    key = (baseline_id, candidate_id)
    if key not in registry:
        raise ArmProfileError(
            f"no campaign_cap_usd configured for baseline={baseline_id!r}, "
            f"candidate={candidate_id!r}"
        )
    val = registry[key]
    if isinstance(val, (int, float)):
        return float(val)
    return val.campaign_cap_usd


def lookup_sql_campaign_cap_usd(
    *,
    baseline_id: str,
    candidate_id: str,
    caps: dict[tuple[str, str], CampaignCaps | float] | None = None,
    caps_path: Path | None = None,
) -> float:
    """Resolve SQL lane cap; requires explicit sql_campaign_cap_usd (no RAG inherit)."""
    registry = caps if caps is not None else load_campaign_caps(caps_path)
    key = (baseline_id, candidate_id)
    if key not in registry:
        raise ArmProfileError(
            "no sql campaign cap configured for "
            f"baseline={baseline_id!r}, candidate={candidate_id!r}"
        )
    val = registry[key]
    if isinstance(val, (int, float)):
        return float(val)
    if val.sql_campaign_cap_usd is None:
        raise ArmProfileError(
            "no sql campaign cap configured for "
            f"baseline={baseline_id!r}, candidate={candidate_id!r}"
        )
    return val.sql_campaign_cap_usd


def _parse_profile(raw: object, *, source: str) -> ArmProfile:
    if not isinstance(raw, dict):
        raise ArmProfileError(f"{source}: profile must be a mapping")

    unknown = set(raw) - _KNOWN_PROFILE_KEYS
    if unknown:
        raise ArmProfileError(f"{source}: unknown key(s) {sorted(unknown)}")

    try:
        model = ArmProfileModel.model_validate(raw)
    except ValidationError as exc:
        raise ArmProfileError(f"{source}: {exc}") from exc

    if model.provider_order is not None:
        provider_order = list(model.provider_order)
    elif model.provider == "deepseek_direct":
        provider_order = []
    else:
        provider_order = list(EVAL_DEFAULT_ORDER)

    if model.api_class is not None:
        api_class = model.api_class
    elif model.provider == "openrouter":
        api_class = "openrouter_chat"
    elif model.provider == "deepseek_direct":
        api_class = "deepseek_chat"
    elif model.provider == "openai":
        api_class = "responses"
    else:
        api_class = "chat_completions"

    has_sql_timeout = "sql_request_timeout_s" in model.model_fields_set

    return ArmProfile(
        id=model.id,
        arm_name=model.arm_name,
        provider=model.provider,
        requested_model=model.requested_model,
        allowed_actual_models=tuple(model.allowed_actual_models),
        harness_output_cap_tokens=model.harness_output_cap_tokens,
        attested_output_cap_tokens=model.attested_output_cap_tokens,
        reasoning_effort=model.reasoning_effort,
        verbosity=model.verbosity,
        request_timeout_s=model.request_timeout_s,
        provider_order=provider_order,
        deployment_attestation=model.deployment_attestation.model_dump(),
        est_answer_usd=model.est_answer_usd,
        chat_deployment=model.chat_deployment,
        api_class=api_class,
        sql_request_timeout_s=model.sql_request_timeout_s,
        has_sql_request_timeout=has_sql_timeout,
    )


def _resolve_profile_extends(
    raw_by_id: dict[str, dict[str, object]],
    profile_id: str,
    *,
    stack: tuple[str, ...] = (),
) -> dict[str, object]:
    """Shallow-merge ``extends`` bases; child keys win. Fail-closed on cycles/missing."""
    if profile_id in stack:
        chain = " -> ".join((*stack, profile_id))
        raise ArmProfileError(f"arm profile extends cycle: {chain}")
    raw = raw_by_id.get(profile_id)
    if raw is None:
        raise ArmProfileError(f"arm profile extends unknown id: {profile_id!r}")
    extends = raw.get(_EXTENDS_KEY)
    if extends is None:
        return dict(raw)
    if not isinstance(extends, str) or not extends:
        raise ArmProfileError(f"{profile_id}: extends must be a non-empty profile id string")
    base = _resolve_profile_extends(raw_by_id, extends, stack=(*stack, profile_id))
    merged = dict(base)
    for key, value in raw.items():
        if key == _EXTENDS_KEY:
            continue
        merged[key] = value
    return merged


def load_arm_profiles(directory: Path | None = None) -> dict[str, ArmProfile]:
    """Load all YAML arm profiles from *directory* (default: evals/experiments/arm_profiles)."""
    profile_dir = directory if directory is not None else _DEFAULT_PROFILES_DIR
    if not profile_dir.is_dir():
        raise ArmProfileError(f"arm profile directory not found: {profile_dir}")

    yaml_files = sorted(profile_dir.glob("*.yaml"))
    if not yaml_files:
        raise ArmProfileError(f"no arm profile YAML files in {profile_dir}")

    raw_by_id: dict[str, dict[str, object]] = {}
    source_by_id: dict[str, str] = {}
    for path in yaml_files:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ArmProfileError(f"{path}: profile must be a mapping")
        profile_id = loaded.get("id")
        if not isinstance(profile_id, str) or not profile_id:
            raise ArmProfileError(f"{path}: missing or invalid id")
        if profile_id in raw_by_id:
            raise ArmProfileError(f"duplicate profile id: {profile_id!r}")
        raw_by_id[profile_id] = loaded
        source_by_id[profile_id] = str(path)

    profiles: dict[str, ArmProfile] = {}
    for profile_id in raw_by_id:
        merged = _resolve_profile_extends(raw_by_id, profile_id)
        merged.pop(_EXTENDS_KEY, None)
        profile = _parse_profile(merged, source=source_by_id[profile_id])
        if profile.id != profile_id:
            raise ArmProfileError(
                f"{source_by_id[profile_id]}: resolved id {profile.id!r} != file id {profile_id!r}"
            )
        profiles[profile.id] = profile

    return profiles


def campaign_uses_openrouter(config: CampaignConfig) -> bool:
    """True when any campaign arm routes through OpenRouter."""
    return config.baseline.provider == "openrouter" or config.candidate.provider == "openrouter"


def campaign_uses_deepseek_direct(config: CampaignConfig) -> bool:
    """True when any campaign arm routes through DeepSeek first-party API."""
    return (
        config.baseline.provider == "deepseek_direct"
        or config.candidate.provider == "deepseek_direct"
    )


def profile_to_route_controls(
    profile: ArmProfile,
    *,
    effort_override: str | None = None,
) -> OpenRouterControls | None:
    """Build OpenRouter controls for one profile, or None when the arm is Azure."""
    if profile.provider != "openrouter":
        return None
    effort = effort_override if effort_override is not None else profile.reasoning_effort
    if effort is None:
        effort = default_effort(profile.requested_model) or "low"
    controls = default_eval_controls(
        reasoning_effort=effort,
        provider_order=profile.provider_order,
    )
    assert_openrouter_controls(controls)
    return controls


def profile_to_sql_arm(
    profile: ArmProfile,
    *,
    effort_override: str | None = None,
    request_timeout_s: float | None = None,
    verbosity_override: str | None = None,
) -> SqlArm:
    """Map a registry profile to a frozen SQL arm."""
    deployment = profile.chat_deployment or profile.requested_model
    if request_timeout_s is not None:
        timeout = request_timeout_s
    elif profile.has_sql_request_timeout:
        timeout = profile.sql_request_timeout_s
    else:
        timeout = profile.request_timeout_s
    verbosity = verbosity_override if verbosity_override is not None else profile.verbosity
    projection = profile.freeze_projection()
    use_responses = profile.api_class == "responses"
    return SqlArm(
        name=profile.arm_name,
        chat_deployment=deployment,
        reasoning_effort=effort_override
        if effort_override is not None
        else profile.reasoning_effort,
        request_timeout_s=timeout,
        requested_provider=profile.provider,
        provider_order=tuple(profile.provider_order),
        verbosity=verbosity,
        allowed_actual_models=tuple(profile.allowed_actual_models),
        profile_id=str(projection["id"]),
        harness_output_cap_tokens=int(projection["harness_output_cap_tokens"]),  # type: ignore[arg-type]
        attested_output_cap_tokens=projection["attested_output_cap_tokens"],  # type: ignore[arg-type]
        deployment_attestation=dict(projection["deployment_attestation"]),  # type: ignore[arg-type]
        est_answer_usd=float(projection["est_answer_usd"]),  # type: ignore[arg-type]
        use_responses_api=use_responses,
        api_class=profile.api_class,
    )


def build_sql_pairwise_campaign(
    baseline_id: str,
    candidate_id: str,
    *,
    profiles: dict[str, ArmProfile] | None = None,
    candidate_effort_override: str | None = None,
) -> SqlCampaignConfig:
    """Assemble a pairwise SQL campaign from two arm profile ids."""
    registry = profiles if profiles is not None else load_arm_profiles()
    if baseline_id not in registry:
        raise ArmProfileError(f"unknown SQL baseline profile id: {baseline_id!r}")
    if candidate_id not in registry:
        raise ArmProfileError(f"unknown SQL candidate profile id: {candidate_id!r}")

    baseline = registry[baseline_id]
    candidate = registry[candidate_id]
    baseline_arm = profile_to_sql_arm(baseline)
    candidate_arm = profile_to_sql_arm(
        candidate,
        effort_override=candidate_effort_override or candidate.reasoning_effort,
    )
    arms = (baseline_arm, candidate_arm)
    router_policy = {
        "name": "prod-router",
        "predicate": "is_hard_financial",
        "default_arm": candidate_arm.name,
        "escalated_arm": baseline_arm.name,
        "baseline_profile_id": baseline_id,
        "candidate_profile_id": candidate_id,
    }
    campaign_cap_usd = lookup_sql_campaign_cap_usd(
        baseline_id=baseline_id,
        candidate_id=candidate_id,
    )
    return SqlCampaignConfig(
        baseline_id=baseline_id,
        candidate_id=candidate_id,
        arms=arms,
        router_policy=router_policy,
        baseline_arm=baseline_arm.name,
        candidate_arm=candidate_arm.name,
        campaign_cap_usd=campaign_cap_usd,
        allowed_actual_models={
            baseline_arm.name: baseline.allowed_actual_models,
            candidate_arm.name: candidate.allowed_actual_models,
        },
    )


def _profile_is_reasoning_model(profile: ArmProfile) -> bool:
    """True when the profile's route is a reasoning model on its provider."""
    if profile.provider == "openrouter":
        return resolve_openrouter_spec(profile.requested_model, settings).returns_reasoning
    if profile.provider == "deepseek_direct":
        return True
    if profile.provider == "openai":
        return supported_efforts(profile.requested_model, provider="openai") is not None
    if profile.provider == "azure":
        return needs_azure_reasoning_completion_args(profile.requested_model)
    return False


def build_pairwise_campaign(
    baseline_id: str,
    candidate_id: str,
    *,
    route_effort: str | None = None,
    profiles: dict[str, ArmProfile] | None = None,
    campaign_caps: dict[tuple[str, str], float] | None = None,
) -> CampaignConfig:
    """Assemble a pairwise campaign from two arm profile ids."""
    registry = profiles if profiles is not None else load_arm_profiles()

    if baseline_id not in registry:
        raise ArmProfileError(f"unknown baseline profile id: {baseline_id!r}")
    if candidate_id not in registry:
        raise ArmProfileError(f"unknown candidate profile id: {candidate_id!r}")

    baseline = registry[baseline_id]
    candidate = registry[candidate_id]

    effort = (
        (route_effort.casefold() if route_effort is not None else None)
        or candidate.reasoning_effort
        or default_effort(candidate.requested_model)
        or "low"
    )
    route_controls_by_arm: dict[str, OpenRouterControls] = {}
    baseline_controls = profile_to_route_controls(baseline)
    if baseline_controls is not None:
        route_controls_by_arm[baseline.arm_name] = baseline_controls
    candidate_controls = profile_to_route_controls(candidate, effort_override=effort)
    if candidate_controls is not None:
        route_controls_by_arm[candidate.arm_name] = candidate_controls

    campaign_cap_usd = lookup_campaign_cap_usd(
        baseline_id=baseline_id,
        candidate_id=candidate_id,
        caps=campaign_caps,
    )

    campaign_timeout_s = max(
        baseline.request_timeout_s or settings.model_request_timeout_s,
        candidate.request_timeout_s or settings.model_request_timeout_s,
    )
    apply_discipline = any(
        _profile_is_reasoning_model(profile) for profile in (baseline, candidate)
    )

    answer_arms = {
        baseline.arm_name: AnswerArm(
            baseline.arm_name,
            baseline.provider,
            baseline.requested_model,
            reasoning_effort=baseline.reasoning_effort,
            verbosity=baseline.verbosity,
            request_timeout_s=campaign_timeout_s,
            apply_reasoning_discipline=apply_discipline,
            harness_output_cap_tokens=baseline.harness_output_cap_tokens,
            attested_output_cap_tokens=baseline.attested_output_cap_tokens,
        ),
        candidate.arm_name: AnswerArm(
            candidate.arm_name,
            candidate.provider,
            candidate.requested_model,
            reasoning_effort=effort,
            verbosity=candidate.verbosity,
            request_timeout_s=campaign_timeout_s,
            apply_reasoning_discipline=apply_discipline,
            harness_output_cap_tokens=candidate.harness_output_cap_tokens,
            attested_output_cap_tokens=candidate.attested_output_cap_tokens,
        ),
    }
    timeouts = {arm.name: arm.request_timeout_s for arm in answer_arms.values()}
    if len(set(timeouts.values())) != 1:
        raise ValueError(f"request_timeout_s must be equal across arms; got {timeouts}")

    return CampaignConfig(
        baseline=baseline,
        candidate=candidate,
        campaign_cap_usd=campaign_cap_usd,
        answer_arms=answer_arms,
        route_controls_by_arm=route_controls_by_arm,
    )
