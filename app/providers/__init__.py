"""
Provider abstraction layer.

Pipelines import from here:
    from app.providers import get_chat_model, get_embeddings

This re-exports from the factory, which delegates to the active provider.
"""

from app.providers.factory import get_chat_model, get_embeddings

__all__ = ["get_chat_model", "get_embeddings"]
