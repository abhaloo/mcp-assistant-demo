"""
RAGAS evaluation script for the RAG + SQL system.

Runs evaluation metrics against test cases from eval_dataset.json.
Scores the system on faithfulness, relevance, and correctness.

Usage:
    python scripts/eval/evaluate_ragas.py                  # Run all evals
    python scripts/eval/evaluate_ragas.py --type semantic  # Only RAG evals
    python scripts/eval/evaluate_ragas.py --type structured # Only SQL evals

WHAT IS RAGAS?
RAGAS (Retrieval Augmented Generation Assessment) is a framework that
scores RAG systems using LLM-as-judge. Instead of checking exact string
matches (too brittle), it uses an LLM to evaluate answer quality on
dimensions like faithfulness and relevance, returning scores from 0 to 1.

Think of it like a test suite, but instead of assert expected == actual,
you get a quality score. A faithfulness score of 0.9 means 90% of the
claims in the answer are supported by the retrieved context.

WHY NOT JUST USE PYTEST?
Pytest tests are binary — pass or fail. RAGAS gives you a spectrum.
"The system scored 0.85 on faithfulness" is more useful than "3 of 10
tests failed" because it tells you HOW CLOSE you are and helps you
track improvements over time.
"""

import argparse
import asyncio
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

# Force UTF-8 on stdout/stderr so model output containing characters
# like ≤, →, em-dashes doesn't crash the script on Windows' cp1252 console.
# errors="replace" is a belt — any unencodable char becomes ? instead of
# raising. See insights-log: Windows console encoding tripwire (2026-05-15).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# NOTE: ragas 0.4.3 has a bug where metrics from ragas.metrics.collections
# don't pass the isinstance(m, Metric) check in evaluate(). The deprecated
# imports from ragas.metrics use the old base class that works. Switch to
# collections imports once ragas fixes this (likely v0.5+).
import warnings  # noqa: E402

from openai import AzureOpenAI, OpenAI  # noqa: E402
from ragas import evaluate  # noqa: E402
from ragas.dataset_schema import EvaluationDataset, SingleTurnSample  # noqa: E402
from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: E402
from ragas.llms import llm_factory  # noqa: E402

from app.providers import get_embeddings  # noqa: E402
from app.providers.azure_credential import (  # noqa: E402
    COGNITIVE_SERVICES_SCOPE,
    get_token_provider,
)

warnings.filterwarnings("ignore", category=DeprecationWarning, module="ragas")
from ragas.metrics import (  # noqa: E402
    AnswerRelevancy,
    ContextPrecision,
    FactualCorrectness,
    Faithfulness,
)

from app.config import settings  # noqa: E402
from app.eval.dataset import load_eval_cases  # noqa: E402
from app.eval.sql.agent.access import ScopedSqlPolicy, ensure_scoped_sql_access  # noqa: E402
from app.eval.sql.agent.agent import get_sql_chain  # noqa: E402
from app.rag.chains.document_chain import (  # noqa: E402
    RagTurnInput,
    get_access_tiers,
    get_rag_chain_with_sources,
)
from app.rag.citations import filter_cited_documents  # noqa: E402
from app.rag.retrieval.metrics import (  # noqa: E402
    citation_precision,
    recall_at_k,
    reciprocal_rank,
    retrieval_hit_at_k,
    snippet_hit_at_n_tokens,
)


def _ragas_judge_client_and_model() -> tuple[OpenAI | AzureOpenAI, str]:
    """Build the OpenAI-compatible client RAGAS llm_factory needs.

    When chat_provider=azure, use Azure AD (same as the RAG chain) — never
    fall through to OpenRouter/base_url, which is empty or credit-gated in
    Azure-only environments.
    """
    if settings.chat_provider == "azure":
        if not settings.azure_endpoint:
            raise SystemExit("chat_provider=azure requires azure_endpoint")
        client = AzureOpenAI(
            azure_endpoint=settings.azure_endpoint,
            api_version=settings.azure_api_version,
            azure_ad_token_provider=get_token_provider(COGNITIVE_SERVICES_SCOPE),
            max_retries=settings.model_max_retries,
            timeout=settings.model_request_timeout_s,
        )
        return client, settings.azure_chat_deployment
    return (
        OpenAI(api_key=settings.model_api_key, base_url=settings.base_url),
        settings.chat_model,
    )


def _write_semantic_run_artifacts(out_dir: Path, cases: list[dict], ragas_df) -> None:
    """Persist deterministic per-case metrics + summary for experiment gates."""
    out_dir.mkdir(parents=True, exist_ok=True)

    def _mean(key: str) -> float:
        vals = [c[key] for c in cases if key in c]
        return sum(vals) / len(vals) if vals else 0.0

    summary = {
        "run_at": datetime.now(UTC).isoformat(),
        "case_count": len(cases),
        "deterministic": {
            "retrieval_hit_at_1": _mean("_det_hit_at_1"),
            "retrieval_hit_at_3": _mean("_det_hit_at_3"),
            "retrieval_hit_at_5": _mean("_det_hit_at_5"),
            "recall_at_5": _mean("_det_recall_at_5"),
            "mrr": _mean("_det_mrr"),
            "snippet_hit_at_5": _mean("_det_snippet_hit_at_5"),
            "citation_precision": _mean("_citation_precision"),
            "citation_parsed_rate": _mean("_citation_parsed"),
        },
        "per_case": [
            {
                "id": c.get("id"),
                "question": c.get("question"),
                "_det_hit_at_5": c.get("_det_hit_at_5"),
                "_det_recall_at_5": c.get("_det_recall_at_5"),
                "_det_mrr": c.get("_det_mrr"),
                "_det_snippet_hit_at_5": c.get("_det_snippet_hit_at_5"),
                "_citation_precision": c.get("_citation_precision"),
                "_citation_parsed": c.get("_citation_parsed"),
            }
            for c in cases
        ],
    }

    if ragas_df is not None:
        metric_cols = ["faithfulness", "answer_relevancy", "context_precision"]
        summary["ragas"] = {
            col: float(ragas_df[col].mean(skipna=True))
            for col in metric_cols
            if col in ragas_df.columns
        }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    failures_dir = Path(settings.failure_store_dir)
    failures_dir.mkdir(parents=True, exist_ok=True)
    run_id = out_dir.name
    for case in cases:
        if case.get("_citation_parsed") == 0 or (case.get("_citation_precision") or 1) < 0.5:
            payload = {
                "run_id": run_id,
                "case_id": case.get("id"),
                "mode": "semantic",
                "question": case.get("question"),
                "citation_parsed": case.get("_citation_parsed"),
                "citation_precision": case.get("_citation_precision"),
                "source_file": case.get("source_file"),
            }
            path = failures_dir / f"{run_id}_{case.get('id')}_semantic.json"
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def run_semantic_eval(
    cases: list[dict], *, collection_name: str | None = None, out_dir: Path | None = None
) -> None:
    """
    Evaluate the RAG chain on semantic questions using RAGAS.

    For each test case:
    1. Run the RAG chain to get an answer + retrieved context
    2. Build a RAGAS SingleTurnSample with the question, answer,
       retrieved context, and expected answer (ground truth)
    3. Score with faithfulness, answer_relevancy, and context_precision

    WHAT EACH METRIC MEASURES:

    faithfulness (0-1):
        "Does the answer ONLY use information from the context?"
        Score of 1.0 = every claim in the answer is traceable to a
        retrieved chunk. Score of 0.5 = half the claims are made up.
        This catches hallucination.

    answer_relevancy (0-1):
        "Does the answer ADDRESS the question?"
        Score of 1.0 = the answer is directly about what was asked.
        Score of 0.3 = the answer talks about something else.
        This catches off-topic responses.

    context_precision (0-1):
        "Are the retrieved chunks actually useful for answering?"
        Score of 1.0 = every chunk contributed to the answer.
        Score of 0.3 = most chunks were irrelevant noise.
        This evaluates your retriever, not the LLM.
    """
    # Step 1: Run each test case through the RAG chain and collect samples
    samples = []
    for case in cases:
        # Get access tiers from the case's role/permissions — same as the API does
        role = case.get("role", "user")
        permissions = case.get("permissions", [])
        access_tiers = get_access_tiers(role, permissions)

        # Run the RAG chain — returns {"answer": str, "sources": [Document, ...]}
        chain = get_rag_chain_with_sources(
            access_tiers=access_tiers, collection_name=collection_name
        )
        result = chain.invoke(RagTurnInput.single_shot(case["question"]).as_chain_dict())

        # Extract the retrieved chunk texts for RAGAS context evaluation
        # RAGAS wants a list of strings, not Document objects
        retrieved_contexts = [doc.page_content for doc in result["sources"]]

        # The expected answer — some cases use "expected_answer",
        # access control cases use "expected_answer_with_access"
        reference = case.get("expected_answer") or case.get("expected_answer_with_access", "")

        # Build the RAGAS sample
        # SingleTurnSample = one question-answer pair with its context
        # Think of it like a test case row in a spreadsheet:
        #   question | system_answer | retrieved_chunks | correct_answer
        sample = SingleTurnSample(
            user_input=case["question"],
            response=result["answer"],
            retrieved_contexts=retrieved_contexts,
            reference=reference,
        )
        samples.append(sample)

        # Deterministic (judge-free) retrieval metrics. Run at the production
        # top-k (5) — equal-tokens-budget primaries belong in the harness
        # because they need direct retriever control. These status metrics
        # let evaluate_ragas.py runs report deterministic numbers alongside RAGAS.
        expected_source = case.get("source_file", "")
        expected_snippet = case.get("expected_snippet", "")
        case["_det_hit_at_1"] = retrieval_hit_at_k(result["sources"], expected_source, k=1)
        case["_det_hit_at_3"] = retrieval_hit_at_k(result["sources"], expected_source, k=3)
        case["_det_hit_at_5"] = retrieval_hit_at_k(result["sources"], expected_source, k=5)
        case["_det_recall_at_5"] = recall_at_k(result["sources"], [expected_source], k=5)
        case["_det_mrr"] = reciprocal_rank(result["sources"], expected_source)
        # Span-level metric at fixed k=5 (huge budget -> k_for_budget falls
        # back to len(sources) which is 5 in production)
        case["_det_snippet_hit_at_5"] = snippet_hit_at_n_tokens(
            result["sources"],
            expected_snippet,
            token_budget=10**9,
            median_chunk_tokens=10**8,
        )
        cited_docs, parsed_ok = filter_cited_documents(result["answer"], result["sources"])
        case["_citation_parsed"] = 1 if parsed_ok else 0
        if expected_source:
            cited_for_metric = (
                [type("S", (), {"source_file": d.metadata.get("source", "")})() for d in cited_docs]
                if parsed_ok
                else []
            )
            case["_citation_precision"] = citation_precision(cited_for_metric, expected_source)
        else:
            case["_citation_precision"] = 1.0

        print(f"  [{case['id']}] answer: {result['answer'][:80]}...")

    # Step 2: Create the evaluation dataset from all samples
    eval_dataset = EvaluationDataset(samples=samples)

    # Step 3: Set up the RAGAS evaluator LLM and embeddings
    # RAGAS uses an LLM as a judge — it reads the answer and context,
    # then scores how faithful/relevant the answer is.
    #
    # RAGAS judge LLM — same provider as the RAG chain (Azure AD or OpenAI).
    eval_client, judge_model = _ragas_judge_client_and_model()
    eval_llm = llm_factory(model=judge_model, client=eval_client)
    # The deprecated AnswerRelevancy metric calls embed_query() internally,
    # which is a LangChain interface method. We get the LangChain embeddings
    # from our provider factory and wrap it for RAGAS compatibility.
    eval_embeddings = LangchainEmbeddingsWrapper(get_embeddings())

    # Step 4: Run RAGAS evaluation
    # evaluate() sends each sample to the judge LLM and scores it
    # on the metrics you specify. Returns a dataset with scores.
    metrics = [
        Faithfulness(llm=eval_llm),
        AnswerRelevancy(llm=eval_llm, embeddings=eval_embeddings),
        ContextPrecision(llm=eval_llm),
    ]

    results = evaluate(
        dataset=eval_dataset,
        metrics=metrics,
    )

    # Step 4.5: Deterministic retrieval metrics — judge-free, run-stable.
    # These are the experiment's primary signal (in the harness; here we
    # report status metrics at production top-k for continuity).
    def _det_mean(key: str) -> float:
        vals = [c[key] for c in cases if key in c]
        return sum(vals) / len(vals) if vals else 0.0

    print("\n=== Deterministic retrieval metrics (judge-free) ===")
    print(f"  retrieval_hit@1: {_det_mean('_det_hit_at_1'):.3f}")
    print(f"  retrieval_hit@3: {_det_mean('_det_hit_at_3'):.3f}")
    print(f"  retrieval_hit@5: {_det_mean('_det_hit_at_5'):.3f}")
    print(f"  recall@5:        {_det_mean('_det_recall_at_5'):.3f}")
    print(f"  MRR:             {_det_mean('_det_mrr'):.3f}")
    print(f"  snippet_hit@5:   {_det_mean('_det_snippet_hit_at_5'):.3f}")
    print(f"  citation_precision: {_det_mean('_citation_precision'):.3f}")
    print(f"  citation_parsed_rate: {_det_mean('_citation_parsed'):.3f}")

    # Step 5: Print results, filtering NaN scores from timeouts/failures
    # RAGAS returns NaN when the judge LLM times out or returns an unparseable
    # response. Including NaN in averages drags scores down artificially —
    # mean([0.8, 0.9, NaN]) = NaN, not 0.85. We exclude them and report
    # how many were dropped so you know if the run was noisy.
    print("\n=== Semantic RAG Evaluation Results ===")

    df = results.to_pandas()
    metric_cols = ["faithfulness", "answer_relevancy", "context_precision"]

    # Report both bounds so NaN dropouts don't silently bias results upward.
    # Optimistic = mean over non-NaN (judge LLM may have skipped hardest cases).
    # Pessimistic = NaN treated as 0 (worst-case if those cases failed completely).
    # The truth lies between these; report both so the reader can judge.
    for col in metric_cols:
        if col not in df.columns:
            continue
        valid = df[col].dropna()
        nan_count = len(df) - len(valid)
        optimistic = valid.mean() if len(valid) > 0 else float("nan")
        pessimistic = df[col].fillna(0.0).mean()
        if nan_count > 0:
            print(
                f"  {col}: {optimistic:.2f} optimistic | {pessimistic:.2f} pessimistic  "
                f"[{len(df)} cases, {nan_count} NaN]"
            )
        else:
            print(f"  {col}: {optimistic:.2f}  [{len(df)} cases]")

    # Per-case breakdown so you can spot which questions are dragging scores
    print("\n  Per-case scores:")
    for _, row in df.iterrows():
        scores = " | ".join(
            f"{col[:5]}={row[col]:.2f}" if not math.isnan(row[col]) else f"{col[:5]}=NaN"
            for col in metric_cols
            if col in df.columns
        )
        print(f"    {row.get('user_input', '?')[:60]}  →  {scores}")

    if out_dir is not None:
        _write_semantic_run_artifacts(out_dir, cases, df)
        print(f"\n  Artifacts -> {out_dir}")

    return results


async def run_structured_eval(cases: list[dict]) -> None:
    """
    Evaluate the SQL agent on structured questions.

    Different from semantic eval because there are no retrieved contexts.
    We evaluate:
    - answer_correctness: does the answer match the expected result?
    - factual_correctness: are the specific facts/numbers correct?

    For structured queries, we can't use faithfulness (no context to be
    faithful to) or context_precision (no retrieved chunks).
    """
    # Only evaluate simple SQL cases that have a clear expected answer.
    # Access control cases (sql-access-control-*) are better as binary
    # pytest tests, not RAGAS spectrum scores.
    simple_cases = [c for c in cases if not c["id"].startswith("sql-access-control")]

    # Explicit CLI/eval/test exemption — see app/rag/sql_access.py.
    ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())

    samples = []
    for case in simple_cases:
        role = case.get("role", "user")
        permissions = case.get("permissions", [])
        access_tiers = get_access_tiers(role, permissions)

        agent = get_sql_chain(
            access_tiers,
            role,
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )

        # SQL agent uses .invoke({"input": question}) and returns {"output": answer}
        # Note: "input"/"output" — different keys from the RAG chain's question/answer
        try:
            result = await asyncio.to_thread(agent.invoke, {"input": case["question"]})
        except Exception as e:
            print(f"  [{case['id']}] ERROR — {e}")
            continue

        # SQL cases may not have expected_answer — skip those for RAGAS
        reference = case.get("expected_answer", "")
        if not reference:
            print(f"  [{case['id']}] answer: {result['output'][:80]}... (no reference, skipped)")
            continue

        sample = SingleTurnSample(
            user_input=case["question"],
            response=result["output"],
            retrieved_contexts=[],  # SQL has no document retrieval
            reference=reference,
        )
        samples.append(sample)
        print(f"  [{case['id']}] answer: {result['output'][:80]}...")

    if not samples:
        print("\nNo SQL cases with expected answers to evaluate.")
        return None

    eval_dataset = EvaluationDataset(samples=samples)

    eval_client, judge_model = _ragas_judge_client_and_model()
    eval_llm = llm_factory(model=judge_model, client=eval_client)

    # SQL eval only uses FactualCorrectness — no context-based metrics
    metrics = [
        FactualCorrectness(llm=eval_llm),
    ]

    results = evaluate(dataset=eval_dataset, metrics=metrics)

    print("\n=== Structured SQL Evaluation Results ===")
    print(results)
    return results


async def run_router_eval() -> None:
    """
    Evaluate router classification accuracy.

    This doesn't use RAGAS — it's a simple accuracy calculation.
    For each router-* test case, check if classify_query returns
    the expected classification.
    """
    from typing import get_args

    from app.models.schemas import QueryType
    from app.rag.query_classifier import StructuredRouteContext, classify_query

    # Load only router cases
    cases = [c for c in load_eval_cases() if c["id"].startswith("router-")]

    correct = 0
    total = len(cases)

    for case in cases:
        # Production always classifies against a context; an unsigned one is
        # what a request with a missing or tampered capability bundle gets.
        # Scoring against it keeps this harness measuring the live router.
        result = await classify_query(case["question"], route_context=StructuredRouteContext())
        # Fail LOUDLY on contract drift: `result == expected` against a changed
        # return shape (e.g. a (query_type, ack) tuple) would silently score 0%
        # while the run completes green (ADR 0031).
        if not isinstance(result, str) or result not in get_args(QueryType):
            raise TypeError(
                f"classify_query contract drift: expected one of {get_args(QueryType)}, "
                f"got {type(result).__name__} {result!r}. Update run_router_eval and "
                "tests/contracts.py::classify_query_result together."
            )
        expected = case["expected_query_type"]
        match = result == expected

        if match:
            correct += 1

        status = "PASS" if match else "FAIL"
        print(f"  [{case['id']}] expected={expected}, got={result} — {status}")

    accuracy = correct / total if total > 0 else 0
    print(f"\n=== Router Evaluation: {correct}/{total} correct ({accuracy:.0%}) ===")
    return accuracy


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
async def main():
    """Run evaluations based on CLI arguments."""
    parser = argparse.ArgumentParser(description="Evaluate RAG + SQL system")
    parser.add_argument(
        "--type",
        choices=["semantic", "structured", "router", "all"],
        default="all",
        help="Which evaluation to run",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="Chroma collection / Azure index for isolated eval runs",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for summary.json + G4 citation failure captures",
    )
    args = parser.parse_args()
    out_dir = Path(args.out_dir) if args.out_dir else None

    if args.type in ("semantic", "all"):
        print("\n--- Running Semantic RAG Evaluation ---")
        cases = load_eval_cases("semantic")
        print(f"Loaded {len(cases)} semantic cases")
        await run_semantic_eval(cases, collection_name=args.collection, out_dir=out_dir)

    if args.type in ("structured", "all"):
        print("\n--- Running Structured SQL Evaluation ---")
        cases = load_eval_cases("structured")
        print(f"Loaded {len(cases)} structured cases")
        await run_structured_eval(cases)

    if args.type in ("router", "all"):
        print("\n--- Running Router Evaluation ---")
        await run_router_eval()


if __name__ == "__main__":
    asyncio.run(main())
