"""
Shared Azure identity for every Azure resource (OpenAI, AI Search, ...).

ONE DefaultAzureCredential, built once and reused.
Process resources container (app.resources.ProcessResources) owns credential lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

    from app.resources import ProcessResources

# Audience scopes — "which resource am I requesting a token FOR".
# Azure AI Search takes the credential directly (no scope needed here); only
# the langchain AzureOpenAI* clients need a token-provider built from a scope.
COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"  # Azure OpenAI


def get_azure_credential(resources: ProcessResources | None = None) -> TokenCredential:
    """One shared DefaultAzureCredential for every Azure client.

    Lazy import: azure-identity ships in the optional [azure] extra, so we
    only import it when an Azure code path actually runs.
    """
    from app.resources import current_process_resources

    owner = resources if resources is not None else current_process_resources()
    return owner.get_azure_credential()


def get_token_provider(
    scope: str,
    *,
    credential: Any = None,
    resources: ProcessResources | None = None,
) -> Callable[[], str]:
    """Build a callable that returns a fresh bearer token for `scope`.

    langchain's AzureOpenAI* clients want a token-provider callable (not a
    raw credential); they call it whenever they need a token, and the
    provider re-mints on expiry. Azure AI Search takes the credential
    directly, so it never uses this.
    """
    from azure.identity import get_bearer_token_provider

    cred = credential if credential is not None else get_azure_credential(resources)
    return get_bearer_token_provider(cred, scope)
