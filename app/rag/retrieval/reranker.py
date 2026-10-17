"""Local cross-encoder rerank-as-filter wrapper for the Chroma retrieval path."""

from __future__ import annotations

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever


class RerankingRetriever(BaseRetriever):
    """Retrieve a wide candidate set, rerank, return top_k above the score floor."""

    base_retriever: BaseRetriever
    top_k: int
    score_floor: float
    model_name: str
    ranker: object | None = None

    model_config = {"arbitrary_types_allowed": True}

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        from flashrank import Ranker, RerankRequest

        candidates = self.base_retriever.invoke(query)
        if not candidates:
            return []

        from app.config import settings

        ranker = self.ranker or Ranker(
            model_name=self.model_name,
            cache_dir=str(settings.rerank_cache_dir),
        )
        passages = [{"id": i, "text": d.page_content} for i, d in enumerate(candidates)]
        request = RerankRequest(query=query, passages=passages)
        ranked = ranker.rerank(request)

        selected: list[Document] = []
        for item in ranked:
            score = float(item.get("score", 0.0))
            if score < self.score_floor:
                continue
            idx = int(item["id"])
            selected.append(candidates[idx])
            if len(selected) >= self.top_k:
                break
        return selected
