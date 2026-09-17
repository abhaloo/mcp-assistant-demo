"""
Provider factory — reads config and returns the right provider's models.

This is the single place that decides which provider module to use.
Adding a new provider means:
1. Create app/providers/new_provider.py with get_chat_model() and get_embeddings()
2. Add a case here in the factory

Pipelines never import provider modules directly — they import from
app.providers (the __init__.py), which delegates here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

if TYPE_CHECKING:
    from app.resources import ProcessResources

from app.config import settings
from app.providers.azure_credential import get_token_provider
from app.providers.capability_model import CapabilityChatModel
from app.providers.deepseek_direct_controls import (
    DeepSeekDirectControls,
    normalize_deepseek_direct_effort,
)
from app.providers.eligibility_policy import assert_eligible
from app.providers.model_purpose import ModelPurpose
from app.providers.model_registry import (
    ModelSpec,
    PolicyViolationError,
    model_spec_from_deployment_target,
    resolve_model_spec,
    token_scope_for_openai_v1_base,
)
from app.providers.openrouter_controls import (
    OpenRouterControls,
    normalize_reasoning_effort,
)
from app.providers.production_catalog import load_production_catalog
from app.providers.route_controls import DeepSeekRouteControls, OpenRouterRouteControls
from app.providers.route_policy import ResolvedModelRoute, RouteContext, get_route_policy


def _resolve_api_key(
    spec: ModelSpec,
    *,
    purpose: ModelPurpose,
    resources: ProcessResources | None = None,
) -> str | Callable[[], str]:
    """Map ModelSpec credential_source to a key or Entra token provider."""
    if spec.credential_source == "entra":
        scope = token_scope_for_openai_v1_base(spec.api_base)
        if resources is not None:
            return get_token_provider(scope, resources=resources)
        return get_token_provider(scope)
    if spec.credential_source == "openrouter":
        return settings.openrouter_api_key
    if spec.credential_source == "deepseek_direct":
        return settings.deepseek_direct_api_key
    if spec.credential_source == "openai":
        if not settings.openai_api_key:
            raise PolicyViolationError(
                purpose=purpose.value,
                credential_source="openai",
                environment=settings.environment,
                model=spec.name,
                detail="openai_api_key required",
            )
        return settings.openai_api_key
    return settings.model_api_key


def _build_openrouter_controls_from_route(
    route: ResolvedModelRoute,
    controls: OpenRouterRouteControls,
) -> OpenRouterControls:
    wire_effort = (
        normalize_reasoning_effort(route.reasoning_effort)
        if route.reasoning_effort is not None
        else None
    )
    pinned = {k: v for k, v in controls.model_dump().items() if v is not None}
    if "order" in pinned:
        pinned["order"] = list(pinned["order"])
    if "only" in pinned:
        pinned["only"] = list(pinned["only"])
    return OpenRouterControls(
        **pinned,
        profile="production",
        reasoning_effort=wire_effort,
        catalog_declared_effort=wire_effort,
        catalog_supported_efforts=route.supported_reasoning_efforts,
    )


def _build_deepseek_direct_controls_from_route(
    route: ResolvedModelRoute,
    controls: DeepSeekRouteControls,
) -> DeepSeekDirectControls:
    return DeepSeekDirectControls(
        thinking_enabled=controls.thinking_enabled,
        reasoning_effort=normalize_deepseek_direct_effort(route.reasoning_effort or "low"),
    )


def _wrap_openai_compat(
    spec: ModelSpec,
    temperature: float,
    *,
    purpose: ModelPurpose,
    controls: OpenRouterControls | None = None,
    deepseek_direct_controls: DeepSeekDirectControls | None = None,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    use_responses_api: bool = False,
    reasoning_summary: bool = False,
    resources: ProcessResources | None = None,
) -> CapabilityChatModel:
    from app.providers import openai_compat_provider
    from app.providers.eligibility_policy import resolve_openrouter_controls_for_env

    openrouter_controls = None
    if spec.credential_source == "openrouter":
        openrouter_controls = resolve_openrouter_controls_for_env(controls, settings.environment)
    inner = openai_compat_provider.get_chat_model(
        spec,
        temperature=temperature,
        api_key=_resolve_api_key(spec, purpose=purpose, resources=resources),
        openrouter_controls=openrouter_controls,
        deepseek_direct_controls=deepseek_direct_controls,
        request_timeout_s=request_timeout_s,
        max_retries=max_retries,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        use_responses_api=use_responses_api,
        reasoning_summary=reasoning_summary,
        resources=resources,
    )
    return CapabilityChatModel(inner=inner, spec=spec)


def _attach_ledger(
    model: BaseChatModel,
    *,
    purpose: ModelPurpose,
    route_key: str | None,
    model_name: str | None,
    provider: str | None,
) -> BaseChatModel:
    """The single seam for the full-fidelity invocation ledger.

    Every ``get_chat_model`` caller funnels through this single call, so all
    six ``ModelPurpose`` values are captured without per-call-site capture
    code. No-ops (with one warning) when the ledger DSN is absent.
    """
    from app.telemetry.invocation_ledger import attach_ledger_observer

    return attach_ledger_observer(
        model,
        purpose=purpose,
        route_key=route_key,
        model_name=model_name,
        provider=provider,
    )


@dataclass(frozen=True)
class _ClientRequest:
    """Everything a client builder needs, gathered before dispatch so every
    builder in ``_CLIENT_BUILDERS`` shares one call signature."""

    spec: ModelSpec
    temperature: float
    purpose: ModelPurpose
    deployment: str
    controls: OpenRouterControls | None
    deepseek_direct_controls: DeepSeekDirectControls | None
    reasoning_effort: str | None
    verbosity: str | None
    request_timeout_s: float | None
    max_retries: int | None
    use_responses_api: bool
    resources: ProcessResources | None = None
    reasoning_summary: bool = False


def _build_openai_compat_client(req: _ClientRequest) -> BaseChatModel:
    openai_direct = req.spec.credential_source == "openai"
    return _wrap_openai_compat(
        req.spec,
        req.temperature,
        purpose=req.purpose,
        controls=req.controls,
        deepseek_direct_controls=req.deepseek_direct_controls,
        request_timeout_s=req.request_timeout_s,
        max_retries=req.max_retries,
        reasoning_effort=req.reasoning_effort if openai_direct else None,
        verbosity=req.verbosity if openai_direct else None,
        use_responses_api=openai_direct and req.reasoning_effort is not None,
        reasoning_summary=openai_direct and req.reasoning_summary,
        resources=req.resources,
    )


def _build_azure_client(req: _ClientRequest) -> BaseChatModel:
    from app.providers import azure_provider

    if req.use_responses_api and req.spec.supports_tools:
        from app.providers.azure_reasoning import needs_azure_reasoning_completion_args
        from app.providers.azure_responses_sql import AzureResponsesSqlChatModel
        from app.providers.reasoning_effort_policy import assert_effort_supported

        if req.reasoning_effort is not None and needs_azure_reasoning_completion_args(
            req.deployment
        ):
            assert_effort_supported(req.deployment, req.reasoning_effort, provider="azure")
        inner = AzureResponsesSqlChatModel(
            deployment=req.deployment,
            model=req.deployment,
            request_timeout_s=req.request_timeout_s,
            reasoning_effort=req.reasoning_effort,
        )
    else:
        inner = azure_provider.get_chat_model(
            temperature=req.temperature,
            deployment=req.deployment,
            reasoning_effort=req.reasoning_effort,
            verbosity=req.verbosity,
            request_timeout_s=req.request_timeout_s,
            max_retries=req.max_retries,
            resources=req.resources,
        )
    return CapabilityChatModel(inner=inner, spec=req.spec)


def _build_legacy_openai_client(req: _ClientRequest) -> BaseChatModel:
    from app.providers import openai_provider

    return openai_provider.get_chat_model(temperature=req.temperature, resources=req.resources)


_CLIENT_BUILDERS: dict[str, Callable[[_ClientRequest], BaseChatModel]] = {
    "openai_compat": _build_openai_compat_client,
    "azure": _build_azure_client,
    "openai": _build_legacy_openai_client,
}


def get_chat_model(
    *,
    purpose: ModelPurpose,
    temperature: float = 0.1,
    deployment: str | None = None,
    route_context: RouteContext | None = None,
    controls: OpenRouterControls | None = None,
    deepseek_direct_controls: DeepSeekDirectControls | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
    use_responses_api: bool = False,
    reasoning_summary: bool = False,
    resources: ProcessResources | None = None,
) -> BaseChatModel:
    """Return a chat model for ``purpose`` after route + eligibility gates.

    Flow: resolve deployment (if omitted) → resolve ModelSpec → assert_eligible
    → bind credentials → return client. Denial raises ``PolicyViolationError``;
    there is no silent fallback to another model.

    ``reasoning_effort``/``verbosity`` reach the Azure branch and the OpenAI-direct
    Responses path. OpenRouter effort already travels via ``controls``
    (``extra_body["reasoning"]``). ``request_timeout_s`` and ``max_retries`` reach
    both branches; ``None`` keeps the provider settings default, while an explicit
    route value (including 0) is wired through. ``reasoning_summary`` asks the
    OpenAI-direct Responses path for a streamed, readable reasoning summary; other
    providers ignore it.
    """
    if purpose == ModelPurpose.rag_answer and not settings.document_rag_enabled:
        raise PolicyViolationError(
            purpose=purpose.value,
            credential_source="",
            environment=settings.environment,
            model=deployment or "",
            detail="Document RAG is disabled in this deployment",
        )

    resolved_route: ResolvedModelRoute | None = None

    if deployment is None:
        resolved_route = get_route_policy().resolve(purpose, route_context)
        deployment = resolved_route.deployment
        if reasoning_effort is None:
            reasoning_effort = resolved_route.reasoning_effort
        if verbosity is None:
            verbosity = resolved_route.verbosity
        if request_timeout_s is None:
            request_timeout_s = resolved_route.request_timeout_s
        if max_retries is None:
            max_retries = resolved_route.max_retries

    if resolved_route is not None:
        target = load_production_catalog().target(resolved_route.target_id)
        spec = model_spec_from_deployment_target(
            target,
            deployment=deployment,
            structured_output_mode=resolved_route.structured_output_mode,
            s=settings,
        )
        controls_block = resolved_route.provider_controls
        deploy_environment = settings.environment
        if resolved_route.provider == "openrouter" and controls is None:
            if not isinstance(controls_block, OpenRouterRouteControls):
                raise PolicyViolationError(
                    purpose=purpose.value,
                    credential_source="openrouter",
                    environment=deploy_environment,
                    model=deployment or "",
                    detail="route is missing typed OpenRouter controls",
                )
            controls = _build_openrouter_controls_from_route(resolved_route, controls_block)
        elif resolved_route.provider == "deepseek_direct" and deepseek_direct_controls is None:
            if not isinstance(controls_block, DeepSeekRouteControls):
                raise PolicyViolationError(
                    purpose=purpose.value,
                    credential_source="deepseek_direct",
                    environment=deploy_environment,
                    model=deployment or "",
                    detail="route is missing typed DeepSeek controls",
                )
            deepseek_direct_controls = _build_deepseek_direct_controls_from_route(
                resolved_route, controls_block
            )
    else:
        try:
            spec = resolve_model_spec(deployment, settings)
        except KeyError as exc:
            if settings.chat_provider == "openai":
                raise ValueError(f"Unknown deployment: {deployment!r}") from exc
            raise

    resolved_controls = controls
    if spec.credential_source == "openrouter":
        from app.providers.eligibility_policy import resolve_openrouter_controls_for_env

        resolved_controls = resolve_openrouter_controls_for_env(controls, settings.environment)

    assert_eligible(
        purpose=purpose,
        spec=spec,
        environment=settings.environment,
        controls=resolved_controls,
    )

    try:
        builder = _CLIENT_BUILDERS[spec.client_kind]
    except KeyError as exc:
        raise ValueError(
            f"unknown client_kind {spec.client_kind!r} for deployment {deployment!r}"
        ) from exc
    model = builder(
        _ClientRequest(
            spec=spec,
            temperature=temperature,
            purpose=purpose,
            deployment=deployment,
            controls=resolved_controls,
            deepseek_direct_controls=deepseek_direct_controls,
            reasoning_effort=reasoning_effort,
            verbosity=verbosity,
            request_timeout_s=request_timeout_s,
            max_retries=max_retries,
            use_responses_api=use_responses_api,
            resources=resources,
            reasoning_summary=reasoning_summary,
        )
    )

    route_key = resolved_route.route_id if resolved_route is not None else None
    provider = resolved_route.provider if resolved_route is not None else spec.credential_source
    return _attach_ledger(
        model,
        purpose=purpose,
        route_key=route_key,
        model_name=spec.name,
        provider=provider,
    )


def get_embeddings(
    *,
    resources: ProcessResources | None = None,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
) -> Embeddings:
    """Return an embeddings model from the active provider."""
    if not settings.document_rag_enabled:
        raise RuntimeError("Document RAG is disabled in this deployment")

    if settings.embedding_provider == "offline":
        from app.providers import offline_provider

        return offline_provider.get_embeddings()

    if settings.chat_provider == "azure":
        from app.providers import azure_provider

        return azure_provider.get_embeddings(
            resources=resources, request_timeout_s=request_timeout_s, max_retries=max_retries
        )

    from app.providers import openai_provider

    return openai_provider.get_embeddings(
        resources=resources, request_timeout_s=request_timeout_s, max_retries=max_retries
    )


__all__ = ["PolicyViolationError", "get_chat_model", "get_embeddings"]
