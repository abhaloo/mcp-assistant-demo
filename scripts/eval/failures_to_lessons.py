"""B2: draft storybloq lesson payloads from CONFIRMED failure clusters.

A confirmed recurring failure becomes a draft lesson storybloq_recommend can surface by
relevance to a coding agent. Never writes .story/; emits payloads a human applies via the
storybloq_lesson_create MCP tool after review.

    python scripts/eval/failures_to_lessons.py --min-count 2
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.eval.diagnose_failures import cluster_failures, load_failures  # noqa: E402


def cluster_to_lesson(cluster: dict) -> dict:
    cases = ", ".join(cluster["case_ids"])
    return {
        "title": f"SQL agent recurring miss: {cluster['family']} / {cluster['bucket']}",
        "content": (
            f"{cluster['count']} confirmed failures in the {cluster['family']} family "
            f"({cluster['bucket']}). Cases: {cases}. Lever hint: {cluster['lever_hint']} "
            f"Sample: {cluster.get('sample_question')}. Check these first when touching the "
            f"SQL agent prompt/graph for this family."
        ),
        "source": "postmortem",
        "tags": ["sql-agent", "failure-cluster", cluster["family"]],
    }


def main_with_args(*, dir: str, min_count: int, out: str) -> int:
    confirmed = load_failures(dir, status="confirmed")
    clusters = [c for c in cluster_failures(confirmed) if c["count"] >= min_count]
    payloads = [cluster_to_lesson(c) for c in clusters]
    Path(out).write_text(json.dumps(payloads, indent=2), encoding="utf-8")
    return len(payloads)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Draft storybloq lessons from confirmed failure clusters"
    )
    ap.add_argument("--dir", default="evals/failures")
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--out", default="evals/failures/_lessons-draft.json")
    args = ap.parse_args()
    n = main_with_args(dir=args.dir, min_count=args.min_count, out=args.out)
    print(
        f"{n} draft lesson(s) -> {args.out}. Review, then apply each via storybloq_lesson_create."
    )


if __name__ == "__main__":
    main()
