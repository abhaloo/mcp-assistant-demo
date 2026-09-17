"""Provider invocation evidence and metadata extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from langchain_openai import AzureChatOpenAI

from app.config import settings


@dataclass(frozen=True)
class InvocationEvidence:
    requested_provider: str
    requested_model: str
    actual_provider: str
    actual_provider_source: str
    actual_provider_endpoint: str | None
    actual_model: str
    actual_model_source: str
    request_id: str
    request_id_source: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    reasoning_text: str | None
    reasoning_evidence: dict[str, Any] | None
    response_metadata: dict[str, Any]


def describe_invocation(
    raw_llm: object,
    message: object,
    *,
    model_spec: object = None,
    chat_deployment: str | None = None,
) -> InvocationEvidence:
    """Extract and describe provider invocation evidence from an LLM response message."""
    usage = getattr(message, "usage_metadata", None) or {}
    if not isinstance(usage, dict):
        usage = dict(usage) if hasattr(usage, "items") else {}
    response_metadata = getattr(message, "response_metadata", None) or {}
    if not isinstance(response_metadata, dict):
        response_metadata = dict(response_metadata) if hasattr(response_metadata, "items") else {}

    credential_source = getattr(model_spec, "credential_source", None)
    client_kind = getattr(model_spec, "client_kind", None)
    if credential_source == "entra":
        requested_provider = "azure" if client_kind == "azure" else "azure_foundry"
    else:
        requested_provider = str(credential_source or settings.chat_provider)
    requested_model = str(
        getattr(model_spec, "model_id", None)
        or chat_deployment
        or settings.azure_chat_deployment
        or settings.chat_model
    )
    provider_request_id = response_metadata.get("id") or response_metadata.get("request_id")
    provider_model = response_metadata.get("model_name") or response_metadata.get("model")
    request_id = str(provider_request_id or getattr(message, "id", None) or "unavailable")
    if request_id.startswith("run--"):
        request_id = request_id.removeprefix("run--")
    response_provider = response_metadata.get("provider")
    actual_provider = str(response_provider or "unknown")
    actual_provider_source = "provider_response" if response_provider else "unavailable"
    actual_provider_endpoint: str | None = None
    inner_model = getattr(raw_llm, "inner", raw_llm)
    if (
        not response_provider
        and requested_provider == "azure"
        and isinstance(inner_model, AzureChatOpenAI)
    ):
        actual_provider_endpoint = urlparse(str(inner_model.azure_endpoint or "")).hostname
        expected_endpoint = urlparse(settings.azure_endpoint or "").hostname
        if (
            actual_provider_endpoint
            and actual_provider_endpoint == expected_endpoint
            and str(inner_model.deployment_name or "") == requested_model
        ):
            actual_provider = "azure"
            actual_provider_source = "azure_client_type_endpoint_and_deployment"
    reasoning_evidence = response_metadata.get("reasoning_evidence")
    reasoning_text = None
    if isinstance(reasoning_evidence, dict):
        raw_text = reasoning_evidence.get("reasoning_text")
        reasoning_text = raw_text if isinstance(raw_text, str) else None

    return InvocationEvidence(
        requested_provider=requested_provider,
        requested_model=requested_model,
        actual_provider=actual_provider,
        actual_provider_source=actual_provider_source,
        actual_provider_endpoint=actual_provider_endpoint,
        actual_model=str(provider_model or "unknown"),
        actual_model_source="provider_response" if provider_model else "unavailable",
        request_id=request_id,
        request_id_source="provider" if provider_request_id else "callback",
        finish_reason=str(response_metadata.get("finish_reason") or "unknown"),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        reasoning_text=reasoning_text,
        reasoning_evidence=reasoning_evidence if isinstance(reasoning_evidence, dict) else None,
        response_metadata=response_metadata,
    )
