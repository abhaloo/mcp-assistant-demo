"""Harvest PRODUCTION failures into the failure store (A2b).

PRIMARY signal: user thumbs-down. /api/feedback (A2a) records create_feedback(run_id, score=0)
on the root "ask" run; this pulls those down-voted runs and writes them as source="production",
status="candidate" records (gold_sql=None, failure_kind=None) for human triage. A human
confirms, writes the correct gold_sql, and classifies failure_kind (definitional|structural).

Why feedback and not a text heuristic: the dangerous production failure is a confident-wrong
answer (valid SQL, plausible-but-wrong number). No error, not empty, not a refusal -> a text
heuristic NEVER flags it; only a 👎 (or human audit) does. looks_like_failure is kept as an
opt-in SECONDARY net for hard failures (error/empty/refusal) that drew no vote.

The store write goes through app.eval.failure_store, so the question is Presidio-scrubbed at
the seam (defense-in-depth; LangSmith capture already scrubbed it, but masker recall < 100%).
The generated SQL is NOT pulled — it is deliberately not in LangSmith (ask_service.py:241-252);
the human recovers it at curation.

    python scripts/eval/harvest_production_failures.py --since-hours 24
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config import settings  # noqa: E402
from app.eval.failure_store import write_failures  # noqa: E402

_FEEDBACK_KEY = "user_verdict"
_REFUSAL_MARKERS = ("i can't", "i cannot", "unable to", "could not determine", "no data")


def is_downvote(fb: dict) -> bool:
    """A user thumbs-down: our verdict key with score 0."""
    return fb.get("key") == _FEEDBACK_KEY and (fb.get("score") or 0) == 0


def is_upvote(fb: dict) -> bool:
    """A user thumbs-up: our verdict key with score 1. Not harvested into the store (a 👍 is
    not a failure); read by feedback_metrics.py as a loop-health signal only."""
    return fb.get("key") == _FEEDBACK_KEY and (fb.get("score") or 0) == 1


def looks_like_failure(run: dict) -> bool:
    """SECONDARY net: hard failures (error/empty/refusal) that never drew a vote. Cannot catch
    confident-wrong — that is exactly what the 👎 is for."""
    if run.get("error"):
        return True
    answer = ((run.get("outputs") or {}).get("answer") or "").strip()
    if not answer:
        return True
    return any(m in answer.lower() for m in _REFUSAL_MARKERS)


def run_to_case(run: dict) -> dict:
    """Shape a LangSmith root run into a failure_store 'case' (gold + kind unknown).

    query_type comes from the answer (root-run outputs) so SQL tooling can skip pure-semantic
    (RAG) down-votes — those touched no SQL and would orphan in SQL-shaped triage/clustering."""
    inputs = run.get("inputs") or {}
    body = inputs.get("body") if isinstance(inputs.get("body"), dict) else {}
    question = body.get("question") or inputs.get("question") or ""
    meta = (run.get("extra") or {}).get("metadata") or {}
    outputs = run.get("outputs") or {}
    return {
        "id": f"prod-{run.get('id')}",
        "role": meta.get("role"),
        "question": question,
        "gold_sql": None,
        "query_type": outputs.get("query_type"),
    }


def _summary_for(cases: list[dict], *, heuristic: bool = False) -> dict:
    """Adapt harvested cases to the write_failures(summary, cases) shape."""
    runs = []
    for c in cases:
        if heuristic:
            runs.append(
                {
                    "case_id": c["id"],
                    "match": False,
                    "generated_sql_scrubbed": None,
                    "error_category": None,
                    "reason": "heuristic_error_empty_or_refusal",
                    "chat_deployment": None,
                }
            )
        else:
            runs.append(
                {
                    "case_id": c["id"],
                    "match": False,
                    "generated_sql_scrubbed": None,
                    "error_category": None,
                    "reason": "downvoted",
                    "chat_deployment": None,
                }
            )
    return {
        "cases": [
            {
                "case_id": c["id"],
                "pass_rate": 0.0,
                "valid_sql_rate": None,
                "flaky": False,
                "degenerate_candidate": False,
            }
            for c in cases
        ],
        "runs": runs,
    }


def _as_dict(obj) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj.dict() if hasattr(obj, "dict") else dict(obj)


def main() -> None:
    ap = argparse.ArgumentParser(description="Harvest thumbs-down production failures")
    ap.add_argument("--since-hours", type=int, default=24)
    ap.add_argument("--run-id", default="prod-harvest")
    ap.add_argument(
        "--include-heuristic",
        action="store_true",
        help="ALSO include error/empty/refusal runs with no vote (secondary net)",
    )
    args = ap.parse_args()

    from langsmith import Client

    from scripts.eval.harvest_eval_cases import build_run_filter

    client = Client()
    runs = [
        _as_dict(r)
        for r in client.list_runs(
            project_name=settings.langsmith_project,
            is_root=True,
            filter=build_run_filter(args.since_hours),
        )
    ]
    by_id = {str(r.get("id")): r for r in runs}

    feedback = [
        _as_dict(fb)
        for fb in client.list_feedback(run_ids=list(by_id), feedback_key=[_FEEDBACK_KEY])
    ]
    downvote_ids = {str(fb.get("run_id")) for fb in feedback if is_downvote(fb)} & set(by_id)
    heuristic_ids: set[str] = set()
    if args.include_heuristic:
        heuristic_ids = {rid for rid, r in by_id.items() if looks_like_failure(r)}
    selected = downvote_ids | heuristic_ids

    if not selected:
        print("no down-voted production runs in window")
        return

    downvote_cases = [run_to_case(by_id[rid]) for rid in downvote_ids]
    heuristic_only_cases = [run_to_case(by_id[rid]) for rid in (heuristic_ids - downvote_ids)]
    cases = downvote_cases + heuristic_only_cases
    semantic = sum(1 for c in cases if c.get("query_type") == "semantic")

    paths: list[Path] = []
    if downvote_cases:
        paths.extend(
            write_failures(
                _summary_for(downvote_cases, heuristic=False),
                downvote_cases,
                out_dir=settings.failure_store_dir,
                run_id=args.run_id,
                mode="prod",
                source="production",
            )
        )
    if heuristic_only_cases:
        paths.extend(
            write_failures(
                _summary_for(heuristic_only_cases, heuristic=True),
                heuristic_only_cases,
                out_dir=settings.failure_store_dir,
                run_id=args.run_id,
                mode="prod",
                source="production",
            )
        )
    note = ""
    if semantic:
        note = f" ({semantic} pure-RAG: tagged semantic, excluded from SQL learning)"
    print(
        f"wrote {len(paths)} production failure candidate(s){note} "
        f"-> {settings.failure_store_dir}. "
        f"Triage: confirm, write gold_sql, set failure_kind + status=confirmed."
    )


if __name__ == "__main__":
    main()
