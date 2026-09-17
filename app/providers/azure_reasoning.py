"""Azure reasoning-model wire conventions (gpt-5*, Luna, o-series).

Judge (RAGAS) and LangChain answer paths must agree: these deployments reject
``max_tokens`` plus non-default sampling and expect ``max_completion_tokens``.
"""

from __future__ import annotations


def needs_azure_reasoning_completion_args(deployment: str) -> bool:
    """True when an Azure deployment needs max_completion_tokens, not max_tokens."""
    m = deployment.lower()
    return any(token in m for token in ("gpt-5", "luna", "o1", "o3", "o4"))


def coerce_azure_reasoning_invoke_kwargs(kwargs: dict) -> dict:
    """Map answer-path invoke kwargs to Azure reasoning wire shape."""
    out = dict(kwargs)
    if "max_tokens" in out:
        ceiling = out.pop("max_tokens")
        out["max_completion_tokens"] = max(int(out.get("max_completion_tokens") or 0), int(ceiling))
    out.pop("temperature", None)
    out.pop("top_p", None)
    return out
