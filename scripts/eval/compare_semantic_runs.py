"""Compare two semantic eval summary.json files with paired bootstrap CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.rag.retrieval.metrics import bootstrap_ci


def _load_vector(path: Path, key: str) -> list[float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [float(row[key]) for row in data["per_case"] if key in row and row[key] is not None]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap CI between two semantic eval runs")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--metrics",
        default="recall_at_5,snippet_hit_at_5,citation_precision",
        help="Comma-separated deterministic metric keys (without _det_ prefix in summary)",
    )
    args = parser.parse_args()

    key_map = {
        "recall_at_5": "_det_recall_at_5",
        "snippet_hit_at_5": "_det_snippet_hit_at_5",
        "citation_precision": "_citation_precision",
        "citation_parsed_rate": "_citation_parsed",
        "mrr": "_det_mrr",
    }

    print(f"baseline:  {args.baseline}")
    print(f"candidate: {args.candidate}\n")

    for metric in args.metrics.split(","):
        metric = metric.strip()
        case_key = key_map.get(metric, metric)
        b = _load_vector(args.baseline, case_key)
        c = _load_vector(args.candidate, case_key)
        if len(b) != len(c):
            raise SystemExit(f"length mismatch for {metric}: {len(b)} vs {len(c)}")
        lo, hi = bootstrap_ci(b, c)
        delta = sum(c) / len(c) - sum(b) / len(b)
        print(f"{metric:22s} delta={delta:+.3f}  CI95=[{lo:+.3f}, {hi:+.3f}]")


if __name__ == "__main__":
    main()
