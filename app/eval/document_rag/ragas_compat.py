"""Make ragas 0.4.x importable on langchain-community 0.4+.

ragas.llms.base does ``from langchain_community.chat_models.vertexai import
ChatVertexAI`` at module import time. That submodule was removed from
langchain-community 0.4.x (Google models moved to langchain-google-*).
Without a stub, every ``import ragas`` fails before any evaluate path runs.

The stub is only for ``isinstance`` membership in ragas's
``MULTIPLE_COMPLETION_SUPPORTED`` list — we never construct ChatVertexAI.
"""

from __future__ import annotations

import sys
import types


def ensure_ragas_importable() -> None:
    """Install a ChatVertexAI stub if the real submodule is missing."""
    try:
        from langchain_community.chat_models.vertexai import ChatVertexAI  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    mod_name = "langchain_community.chat_models.vertexai"
    if mod_name in sys.modules:
        return

    mod = types.ModuleType(mod_name)

    class ChatVertexAI:  # noqa: N801 — match upstream symbol name
        """Placeholder so ragas can import; not used for inference."""

    mod.ChatVertexAI = ChatVertexAI
    sys.modules[mod_name] = mod

    # Ensure parent package exposes the submodule attribute if already imported.
    parent = sys.modules.get("langchain_community.chat_models")
    if parent is not None:
        setattr(parent, "vertexai", mod)
