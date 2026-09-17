"""
OpenAI-compatible provider.

Covers any provider that speaks the OpenAI API format:
- GitHub Models (models.inference.ai.azure.com)
- OpenRouter (openrouter.ai/api/v1)
- Groq (api.groq.com/openai/v1)
- Ollama (localhost:11434/v1)
- OpenAI itself (api.openai.com/v1)

All of these work with LangChain's ChatOpenAI and OpenAIEmbeddings
by changing base_url and api_key. No format translation needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings
from app.providers.http_clients import (
    embedding_client_kwargs,
    get_async_http_client,
    get_sync_http_client,
)

if TYPE_CHECKING:
    from app.resources import ProcessResources


def get_chat_model(
    temperature: float = 0.1,
    *,
    resources: ProcessResources | None = None,
) -> ChatOpenAI:
    """Return a ChatOpenAI instance configured from settings."""
    return ChatOpenAI(
        model=settings.chat_model,
        temperature=temperature,
        openai_api_key=settings.model_api_key,
        base_url=settings.base_url,
        max_retries=settings.model_max_retries,
        stream_usage=settings.chat_stream_usage,
        http_client=get_sync_http_client(resources),
        http_async_client=get_async_http_client(resources),
    )


def get_embeddings(
    *,
    resources: ProcessResources | None = None,
    request_timeout_s: float | None = None,
    max_retries: int | None = None,
) -> OpenAIEmbeddings:
    """Return an OpenAIEmbeddings instance configured from settings.

    A request timeout or retry bound switches to the document HTTP clients so
    the process-wide clients keep their defaults.
    """
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        openai_api_key=settings.model_api_key,
        openai_api_base=settings.base_url,
        **embedding_client_kwargs(
            resources, request_timeout_s=request_timeout_s, max_retries=max_retries
        ),
    )
