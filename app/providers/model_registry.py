"""In-repo model registry: deployment name → capability-aware ModelSpec."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.providers.production_catalog import StructuredOutputMode

CredentialSource = Literal["entra", "openrouter", "deepseek_direct", "openai"]

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.config import Settings
    from app.providers.context_budget import ContextProfile
    from app.providers.production_catalog import DeploymentTarget


class CapabilityError(Exception):
    """Raised when a model lacks a required capability (e.g. bind_tools)."""

    def __init__(self, model: str, capability: str) -> None:
        self.model = model
        self.capability = capability
        suffix = " (supports_tools=false)" if capability == "tools" else ""
        super().__init__(f"Model {model!r} does not support capability {capability!r}{suffix}")


class PolicyViolationError(Exception):
    """Raised when purpose/environment/policy denies a model+credential combination."""

    def __init__(
        self,
        *,
        purpose: str,
        credential_source: str,
        environment: str,
        model: str,
        detail: str = "",
    ) -> None:
        self.purpose = purpose
        self.credential_source = credential_source
        self.environment = environment
        self.model = model
        self.detail = detail
        msg = (
            f"Policy denies {credential_source!r} for purpose {purpose!r} "
            f"in {environment!r} (model={model!r})"
        )
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


@dataclass(frozen=True)
class ModelSpec:
    name: str
    client_kind: str
    api_base: str
    model_id: str
    supports_tools: bool
    structured_output_mode: StructuredOutputMode
    returns_reasoning: bool
    auth: str
    credential_source: CredentialSource
    context_profile: ContextProfile | None = None


def token_scope_for_openai_v1_base(api_base: str) -> str:
    """Return the Entra audience scope for an OpenAI v1-compatible base URL."""
    from app.providers.azure_credential import COGNITIVE_SERVICES_SCOPE

    if "services.ai.azure.com" in api_base:
        return "https://ai.azure.com/.default"
    return COGNITIVE_SERVICES_SCOPE


def resolve_openrouter_spec(
    deployment: str, s: Settings, *, returns_reasoning: bool | None = None
) -> ModelSpec:
    """Build a dynamic ModelSpec for an OpenRouter vendor/model deployment.

    ``returns_reasoning`` lets a caller who knows the truth state it explicitly.
    When omitted, falls back to a "deepseek" substring heuristic and logs that the
    value was guessed -- the heuristic is wrong for the next non-DeepSeek reasoning
    model, and a silent wrong guess is what produced a spurious ROUTE-BROKEN finding.
    """
    if returns_reasoning is None:
        returns_reasoning = "deepseek" in deployment.casefold()
        logger.warning(
            "resolve_openrouter_spec: guessed returns_reasoning=%s for %r from a "
            "'deepseek' substring heuristic; pass returns_reasoning explicitly to "
            "avoid a wrong guess for a non-DeepSeek reasoning model.",
            returns_reasoning,
            deployment,
        )
    return ModelSpec(
        name=deployment,
        client_kind="openai_compat",
        api_base=s.openrouter_base_url.rstrip("/"),
        model_id=deployment,
        supports_tools=True,
        structured_output_mode="none",
        returns_reasoning=returns_reasoning,
        auth="api_key",
        credential_source="openrouter",
    )


def normalize_openai_v1_base(endpoint: str) -> str:
    """Strip trailing slash and ensure an OpenAI v1 suffix for Foundry/Azure hosts."""
    base = endpoint.rstrip("/")
    if base.endswith("/openai/v1"):
        return base
    return f"{base}/openai/v1"


def _normalize_name(name: str) -> str:
    return name.strip().casefold()


def _direct_deepseek_spec(s: Settings) -> ModelSpec:
    model = s.deepseek_direct_model
    return ModelSpec(
        name=model,
        client_kind="openai_compat",
        api_base=s.deepseek_direct_base_url.rstrip("/"),
        model_id=model,
        supports_tools=True,
        structured_output_mode="none",
        returns_reasoning=True,
        auth="api_key",
        credential_source="deepseek_direct",
    )


def azure_deployment_spec(s: Settings, deployment: str) -> ModelSpec:
    """Synthetic ModelSpec for an Azure OpenAI deployment (Entra auth)."""
    from app.providers.azure_reasoning import needs_azure_reasoning_completion_args

    return ModelSpec(
        name=deployment,
        client_kind="azure",
        api_base=s.azure_endpoint or "",
        model_id=deployment,
        supports_tools=True,
        structured_output_mode="json_schema",
        returns_reasoning=needs_azure_reasoning_completion_args(deployment),
        auth="entra",
        credential_source="entra",
    )


def build_registry(s: Settings) -> dict[str, ModelSpec]:
    """Build deployment-name → ModelSpec map from settings. Rejects name collisions."""
    registry: dict[str, ModelSpec] = {}
    seen_normalized: dict[str, str] = {}

    candidates: list[ModelSpec] = []

    if s.deepseek_direct_model and s.deepseek_direct_base_url and s.deepseek_direct_api_key:
        candidates.append(_direct_deepseek_spec(s))

    if s.azure_endpoint:
        if s.azure_chat_deployment:
            candidates.append(azure_deployment_spec(s, s.azure_chat_deployment))
        if s.azure_chat_escalation_deployment:
            candidates.append(azure_deployment_spec(s, s.azure_chat_escalation_deployment))

    for spec in candidates:
        norm = _normalize_name(spec.name)
        if norm in seen_normalized:
            raise ValueError(
                f"Duplicate deployment name collision: {spec.name!r} and {seen_normalized[norm]!r}"
            )
        seen_normalized[norm] = spec.name
        registry[spec.name] = spec

    return registry


def model_spec_from_deployment_target(
    target: DeploymentTarget,
    *,
    deployment: str,
    structured_output_mode: StructuredOutputMode,
    s: Settings,
) -> ModelSpec:
    """Build ModelSpec from catalog target — no slug inference or Azure fallthrough."""
    if target.provider == "azure":
        client_kind = "azure"
        api_base = s.azure_endpoint or ""
        auth = "entra"
    elif target.provider == "openrouter":
        client_kind = "openai_compat"
        auth = "api_key"
        api_base = s.openrouter_base_url.rstrip("/")
    elif target.provider == "deepseek_direct":
        client_kind = "openai_compat"
        auth = "api_key"
        api_base = s.deepseek_direct_base_url.rstrip("/")
    elif target.provider == "openai":
        client_kind = "openai_compat"
        auth = "api_key"
        api_base = (s.openai_base_url or "https://api.openai.com/v1").rstrip("/")
    else:
        raise ValueError(f"unknown catalog provider: {target.provider!r}")

    return ModelSpec(
        name=deployment,
        client_kind=client_kind,
        api_base=api_base,
        model_id=target.model_id,
        supports_tools="tools" in target.capabilities,
        structured_output_mode=structured_output_mode,
        returns_reasoning=bool(target.supported_reasoning_efforts),
        auth=auth,
        credential_source=target.credential_source,  # type: ignore[arg-type]
        context_profile=target.context_profile,
    )


def resolve_model_spec(deployment: str, s: Settings) -> ModelSpec:
    """Resolve a ModelSpec for an explicitly-named ``deployment``.

    The catalog owns every route the request path resolves; this is the
    explicit-deployment seam used by the SQL chain, diagnostics and campaign
    code, which name a deployment directly rather than a purpose.
    """
    norm = _normalize_name(deployment)
    for name, spec in build_registry(s).items():
        if _normalize_name(name) == norm:
            return spec

    if s.openrouter_api_key and "/" in deployment:
        return resolve_openrouter_spec(deployment, s)

    if s.chat_provider == "azure":
        return azure_deployment_spec(s, deployment)

    raise KeyError(f"Unknown deployment: {deployment!r}")
