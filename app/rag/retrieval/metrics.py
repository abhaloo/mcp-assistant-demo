"""Deterministic retrieval metrics. No LLM, no judge — pure data comparison.

Two metric families:

1. **Source-level metrics** (`retrieval_hit_at_k`, `recall_at_k`, `reciprocal_rank`,
   `recall_at_n_tokens`): was the *correct source file* retrieved? Compares
   `metadata['source']` against `case['source_file']` after path normalization.

2. **Span-level metric** (`snippet_hit_at_n_tokens`): did the verbatim
   `expected_snippet` from the eval case appear in any retrieved chunk's text?
   Whitespace-normalized substring check.

`top_k_for_budget` computes equal-tokens-budget top-k so the primary metrics
control for the chunk-size x top-k confound — larger chunks get a smaller k
under a fixed token budget, keeping the fraction of corpus retrieved
approximately constant.

`bootstrap_ci` computes a paired-bootstrap 95% CI on the (candidate - baseline)
delta. Load-bearing at N=62: a 3pp delta sits inside 1 standard error, so a
point estimate alone says little.
"""

from __future__ import annotations

import math
import random
import re

from app.rag.retrieval.source_normalizer import normalize_source


def retrieval_hit_at_k(retrieved_docs, expected_source: str, k: int) -> int:
    """1 if the normalized `expected_source` appears in any of the first k
    retrieved docs' sources, else 0."""
    target = normalize_source(expected_source)
    if not target:
        return 0
    for doc in retrieved_docs[:k]:
        source = doc.metadata.get("source", "") if hasattr(doc, "metadata") else ""
        if normalize_source(source) == target:
            return 1
    return 0


def recall_at_k(retrieved_docs, expected_sources: list[str], k: int) -> float:
    """Fraction of `expected_sources` that appear in the first k retrieved docs."""
    if not expected_sources:
        return 0.0
    targets = {normalize_source(s) for s in expected_sources if s}
    if not targets:
        return 0.0
    retrieved_sources = {
        normalize_source(d.metadata.get("source", ""))
        for d in retrieved_docs[:k]
        if hasattr(d, "metadata")
    }
    return len(targets & retrieved_sources) / len(targets)


def reciprocal_rank(retrieved_docs, expected_source: str) -> float:
    """1/rank of the first retrieved chunk whose source matches `expected_source`.
    0 if no match found in the retrieved list."""
    target = normalize_source(expected_source)
    if not target:
        return 0.0
    for i, doc in enumerate(retrieved_docs, start=1):
        if not hasattr(doc, "metadata"):
            continue
        if normalize_source(doc.metadata.get("source", "")) == target:
            return 1.0 / i
    return 0.0


def top_k_for_budget(token_budget: int, median_chunk_tokens: int) -> int:
    """Top-k that retrieves approximately `token_budget` tokens of content total.

    For chunks larger than the budget, returns 1 (the minimum useful k).
    """
    if median_chunk_tokens <= 0:
        return 1
    return max(1, math.ceil(token_budget / median_chunk_tokens))


def recall_at_n_tokens(
    retrieved_docs,
    expected_sources: list[str],
    token_budget: int,
    median_chunk_tokens: int,
) -> float:
    """`recall_at_k` with k computed from a token budget. Controls for the
    chunk-size x top-k confound that inflates fixed-k recall for larger chunks."""
    k = top_k_for_budget(token_budget, median_chunk_tokens)
    return recall_at_k(retrieved_docs, expected_sources, k)


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def snippet_hit_at_n_tokens(
    retrieved_docs,
    expected_snippet: str,
    token_budget: int,
    median_chunk_tokens: int,
) -> int:
    """1 if `expected_snippet` appears in any retrieved chunk's `page_content`
    under the equal-tokens budget, else 0. Whitespace-normalized substring check."""
    if not expected_snippet:
        return 0
    k = top_k_for_budget(token_budget, median_chunk_tokens)
    target = _normalize_ws(expected_snippet)
    for doc in retrieved_docs[:k]:
        body = _normalize_ws(getattr(doc, "page_content", ""))
        if target in body:
            return 1
    return 0


def citation_precision(cited_sources: list, expected_source: str) -> float:
    """Fraction of cited sources whose normalized file matches gold."""
    if not cited_sources and not expected_source:
        return 1.0
    if not cited_sources:
        return 0.0
    target = normalize_source(expected_source)
    if not target:
        return 0.0
    matches = 0
    for source in cited_sources:
        source_file = getattr(source, "source_file", None) or (
            source.get("source_file") if isinstance(source, dict) else ""
        )
        if normalize_source(str(source_file)) == target:
            matches += 1
    return matches / len(cited_sources)


def bootstrap_ci(
    baseline_scores: list[float],
    candidate_scores: list[float],
    n_iter: int = 10000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Paired-bootstrap 95% CI on the (candidate_mean - baseline_mean) delta.

    Caller must ensure the two lists are ordered by the same case sequence.
    Resamples indices with replacement `n_iter` times, computes the delta of
    means at each resample, returns the (alpha/2, 1 - alpha/2) quantiles.

    With N=62 and p~=0.85, the standard error on a binary metric is ~4.5pp —
    a 3pp delta sits inside 1 SE, so this CI is load-bearing for decisions.
    """
    if len(baseline_scores) != len(candidate_scores):
        raise ValueError("score vectors must have matched length")
    n = len(baseline_scores)
    if n == 0:
        return (0.0, 0.0)
    deltas: list[float] = []
    for _ in range(n_iter):
        idxs = [random.randrange(n) for _ in range(n)]
        b = sum(baseline_scores[i] for i in idxs) / n
        c = sum(candidate_scores[i] for i in idxs) / n
        deltas.append(c - b)
    deltas.sort()
    lo = deltas[int(n_iter * alpha / 2)]
    hi = deltas[int(n_iter * (1 - alpha / 2))]
    return (lo, hi)
