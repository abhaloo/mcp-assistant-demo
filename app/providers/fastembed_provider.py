"""Local FastEmbed embeddings — cache-only, no HuggingFace download."""

from __future__ import annotations

from langchain_core.embeddings import Embeddings

from app.config import settings

_BGE_SMALL_EN_V1_5 = "BAAI/bge-small-en-v1.5"
_BGE_SMALL_DIMENSIONS = 384


class FastEmbedLocalEmbeddings(Embeddings):
    """Wrap fastembed.TextEmbedding with LangChain's Embeddings interface."""

    def __init__(
        self,
        *,
        model_name: str = settings.fastembed_model,
        cache_dir: str = settings.fastembed_cache_dir,
        local_files_only: bool = True,
    ) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise ImportError(
                "fastembed is not installed. Install with: pip install -e '.[fastembed]'"
            ) from exc

        self._model = TextEmbedding(
            model_name=model_name,
            cache_dir=cache_dir,
            local_files_only=local_files_only,
        )
        self.dimensions = _BGE_SMALL_DIMENSIONS if model_name == _BGE_SMALL_EN_V1_5 else None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(vec) for vec in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return list(next(self._model.embed([text])))


def get_embeddings() -> Embeddings:
    return FastEmbedLocalEmbeddings(local_files_only=True)
