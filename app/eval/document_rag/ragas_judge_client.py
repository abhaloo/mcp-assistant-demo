"""RAGAS judge construction + score aggregation (ADR 0015)."""

from __future__ import annotations

import pandas as pd
from openai import AzureOpenAI, OpenAI

from app.config import settings
from app.providers.azure_credential import COGNITIVE_SERVICES_SCOPE, get_token_provider


def get_judge_client(*, timeout_s: float | None = None):
    """Raw OpenAI-compatible client for ragas.llm_factory, routed by chat_provider.

    Azure path must use ``azure_ad_token_provider`` (same as evaluate_ragas) —
    never ``api_key=get_azure_credential()``, which is a TokenCredential object.
    """
    # Luna + RAGAS structured calls often exceed the chat default (60s).
    timeout = timeout_s if timeout_s is not None else max(settings.model_request_timeout_s, 180.0)
    if settings.chat_provider == "azure":
        if not settings.azure_endpoint:
            raise ValueError("chat_provider=azure requires azure_endpoint")
        return AzureOpenAI(
            azure_endpoint=settings.azure_endpoint,
            api_version=settings.azure_api_version,
            azure_ad_token_provider=get_token_provider(COGNITIVE_SERVICES_SCOPE),
            max_retries=settings.model_max_retries,
            timeout=timeout,
        )
    return OpenAI(
        api_key=settings.model_api_key,
        base_url=settings.base_url,
        timeout=timeout,
    )


def _ragas_llm_factory(*, model: str, client):
    from app.eval.document_rag.ragas_compat import ensure_ragas_importable

    ensure_ragas_importable()
    from ragas.llms import llm_factory

    llm = llm_factory(model=model, client=client)
    patch_instructor_llm_args(llm, model=model)
    return llm


def patch_instructor_llm_args(llm: object, *, model: str) -> None:
    """Mutate ragas InstructorLLM.model_args for Azure reasoning deployments."""
    from app.providers.azure_reasoning import needs_azure_reasoning_completion_args

    args = getattr(llm, "model_args", None)
    if not isinstance(args, dict):
        return
    if not needs_azure_reasoning_completion_args(model):
        return
    args.pop("max_tokens", None)
    args.pop("temperature", None)
    args.pop("top_p", None)
    # Reasoning models burn tokens before structured JSON; 2k truncates RAGAS.
    args["max_completion_tokens"] = max(int(args.get("max_completion_tokens") or 0), 8192)


def get_judge_llm(*, deployment: str | None = None, llm_factory_fn=_ragas_llm_factory):
    """Return a ragas llm_factory judge; optional deployment overrides active model."""
    model = deployment or settings.active_chat_model
    return llm_factory_fn(model=model, client=get_judge_client())


def summarize_ragas(df: pd.DataFrame, metrics: list[str]) -> dict[str, dict]:
    """NaN-honest aggregation. optimistic = NaN-dropped mean; pessimistic = NaN→0."""
    out: dict[str, dict] = {}
    for m in metrics:
        col = df[m]
        out[m] = {
            "mean_optimistic": float(col.dropna().mean()) if col.notna().any() else 0.0,
            "mean_pessimistic": float(col.fillna(0).mean()),
            "nan_count": int(col.isna().sum()),
        }
    return out
