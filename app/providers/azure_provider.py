"""
Azure provider — OpenAI models via Azure's OpenAI-compatible inference path.

Both chat (`AzureChatOpenAI`) and embeddings (`AzureOpenAIEmbeddings`) hit
the legacy AOAI endpoint shape:
    https://<resource>.openai.azure.com/openai/deployments/<dep>/...

On a `kind: AIServices` (Foundry) resource, this endpoint is still the
OpenAI-compat surface — Foundry exposes both `.openai.azure.com` (legacy
AOAI compat) and `.services.ai.azure.com` (Foundry inference) on the same
resource. For OpenAI deployments (gpt-*, text-embedding-*) the legacy
path is the stable LangChain-supported one.

Non-OpenAI models (DeepSeek, OpenRouter vendor/model ids) are routed by
the factory through `openai_compat_provider` (ChatOpenAI against
`/openai/v1/` or OpenRouter's API) wrapped in `CapabilityChatModel` —
not through this Azure OpenAI deployment client.

Auth: Microsoft Entra ID (managed identity) via the shared
DefaultAzureCredential in app/providers/azure_credential — no API key.
Locally it uses your `az login`; in Azure it uses the container's managed
identity. Requires the `Cognitive Services OpenAI User` role on the Azure
OpenAI resource for the principal that runs this code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import AzureChatOpenAI, AzureOpenAIEmbeddings

from app.config import settings
from app.providers.azure_credential import COGNITIVE_SERVICES_SCOPE, get_token_provider
from app.providers.azure_reasoning import needs_azure_reasoning_completion_args
from app.providers.http_clients import (
    embedding_client_kwargs,
    get_async_http_client,
    get_sync_http_client,
)
from app.providers.reasoning_effort_policy import assert_effort_supported

if TYPE_CHECKING:
    from app.resources import ProcessResources


def _coerce_temperature_for_deployment(temperature: float, deployment: str | None) -> float:
    """Azure reasoning deployments (gpt-5*, Luna, o-series) reject non-default temperature."""
    dep = deployment or settings.azure_chat_deployment or ""
    if needs_azure_reasoning_completion_args(dep) and temperature != 1.0:
        return 1.0
    return temperature


def get_chat_model(
    temperature: float = 0.1,
    deployment: str | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
    *,
    resources: ProcessResources | None = None,
) -> BaseChatModel:
    """Return an AzureChatOpenAI instance authenticated with managed identity.

    `deployment` overrides settings.azure_chat_deployment for per-query model routing
    (the escalation cascade); None keeps the default. `reasoning_effort`/`verbosity`
    are only sent to reasoning deployments (gpt-5*, Luna, o-series) -- other
    deployments do not accept them, so they are silently dropped for e.g. gpt-4o-mini.
    `request_timeout_s` overrides settings.model_request_timeout_s when given.
    """
    if not settings.azure_endpoint:
        raise RuntimeError("chat_provider='azure' requires azure_endpoint in .env")
    resolved_deployment = deployment or settings.azure_chat_deployment
    resolved_timeout = (
        request_timeout_s if request_timeout_s is not None else settings.model_request_timeout_s
    )
    token_provider = (
        get_token_provider(COGNITIVE_SERVICES_SCOPE, resources=resources)
        if resources is not None
        else get_token_provider(COGNITIVE_SERVICES_SCOPE)
    )
    kwargs: dict = {
        "azure_endpoint": settings.azure_endpoint,
        "azure_ad_token_provider": token_provider,
        "api_version": settings.azure_api_version,
        "azure_deployment": resolved_deployment,
        "temperature": _coerce_temperature_for_deployment(temperature, deployment),
        "max_retries": (max_retries if max_retries is not None else settings.model_max_retries),
        "timeout": resolved_timeout,
        "stream_usage": settings.chat_stream_usage,
        "http_client": get_sync_http_client(resources),
        "http_async_client": get_async_http_client(resources),
    }
    if needs_azure_reasoning_completion_args(resolved_deployment or ""):
        if reasoning_effort is not None:
            assert_effort_supported(resolved_deployment, reasoning_effort, provider="azure")
            kwargs["reasoning_effort"] = reasoning_effort
        if verbosity is not None:
            kwargs["verbosity"] = verbosity
    return AzureChatOpenAI(**kwargs)


def get_embeddings(
    *,
    resources: ProcessResources | None = None,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
) -> Embeddings:
    """Return an AzureOpenAIEmbeddings instance authenticated with managed identity.

    A request timeout or retry bound switches to the document HTTP clients so
    the process-wide clients keep their defaults.
    """
    if not settings.azure_endpoint:
        raise RuntimeError("chat_provider='azure' requires azure_endpoint in .env")
    token_provider = (
        get_token_provider(COGNITIVE_SERVICES_SCOPE, resources=resources)
        if resources is not None
        else get_token_provider(COGNITIVE_SERVICES_SCOPE)
    )
    return AzureOpenAIEmbeddings(
        azure_endpoint=settings.azure_endpoint,
        azure_ad_token_provider=token_provider,
        api_version=settings.azure_api_version,
        azure_deployment=settings.azure_embedding_deployment,
        **embedding_client_kwargs(
            resources, request_timeout_s=request_timeout_s, max_retries=max_retries
        ),
    )
