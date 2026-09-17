"""Pin ragas importability under langchain-community 0.4+."""

from __future__ import annotations


def test_ensure_ragas_importable_allows_import_ragas():
    from app.eval.document_rag.ragas_compat import ensure_ragas_importable

    ensure_ragas_importable()
    import ragas  # noqa: F401
    from ragas import evaluate  # noqa: F401

    assert callable(evaluate)
