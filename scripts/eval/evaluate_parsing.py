"""Test A — parse fidelity.

For each document in the ground-truth manifest, run a parser and score its
tables (TEDS) + reading order against the annotated truth.

Usage:
    python scripts/eval/evaluate_parsing.py --parser pypdf
    python scripts/eval/evaluate_parsing.py --parser unstructured
"""

import argparse
import json
import time
from pathlib import Path

from app.eval.parsing.metrics import evaluate_parse, load_manifest
from app.rag.ingestion.parser_factory import get_parser

FIXTURE_DIR = Path("tests/ingestion/fixtures")
MANIFEST = FIXTURE_DIR / "manifest.jsonl"


def block_label(block) -> str:
    """First line of a block's text — used as its reading-order label."""
    text = (block.content or "").strip()
    return text.splitlines()[0] if text else block.kind


def run(parser_kind: str, manifest: Path = MANIFEST, params: dict | None = None) -> dict:
    parser = get_parser(parser_kind, params or {})
    rows = load_manifest(str(manifest))
    per_doc = []
    for row in rows:
        doc_path = FIXTURE_DIR / row["file"]
        if not doc_path.exists():
            print(f"  SKIP {row['doc_id']}: {doc_path} not present")
            continue
        blocks = parser.parse(doc_path)
        predicted_tables = [b.html for b in blocks if b.kind == "table" and b.html]
        predicted_order = [block_label(b) for b in blocks]
        scored = evaluate_parse(
            predicted_tables=predicted_tables,
            expected_tables=row["expected_tables"],
            predicted_order=predicted_order,
            expected_order=row["expected_reading_order"],
        )
        scored["doc_id"] = row["doc_id"]
        per_doc.append(scored)
        print(
            f"  {row['doc_id']}: TEDS={scored['mean_teds']:.3f} order={scored['reading_order']:.3f}"
        )

    n = len(per_doc) or 1
    summary = {
        "parser": parser_kind,
        "n_docs": len(per_doc),
        "mean_teds": sum(d["mean_teds"] for d in per_doc) / n,
        "reading_order": sum(d["reading_order"] for d in per_doc) / n,
        "per_doc": per_doc,
    }
    return summary


def main():
    ap = argparse.ArgumentParser(description="Parse-fidelity eval (Test A)")
    ap.add_argument("--parser", default="pypdf", help="parser kind: pypdf|unstructured")
    ap.add_argument(
        "--manifest",
        default="manifest.jsonl",
        help="manifest filename under tests/ingestion/fixtures/ (default golden manifest.jsonl)",
    )
    ap.add_argument(
        "--no-ocr",
        action="store_true",
        help="docling only: disable OCR and use the PDF text layer (for digital PDFs)",
    )
    ap.add_argument(
        "--strategy",
        default=None,
        help="unstructured only: partition strategy (fast|hi_res|auto|ocr_only|vlm)",
    )
    ap.add_argument("--out", default=None, help="optional JSON output path")
    args = ap.parse_args()

    params: dict = {}
    if args.no_ocr:
        params["do_ocr"] = False
    if args.strategy:
        params["strategy"] = args.strategy
    params = params or None
    print(f"=== Test A: parse fidelity ({args.parser}, {args.manifest}) ===")
    start = time.time()
    summary = run(args.parser, FIXTURE_DIR / args.manifest, params)
    print(f"\nmean_teds={summary['mean_teds']:.3f}  reading_order={summary['reading_order']:.3f}")
    print(f"({summary['n_docs']} docs in {time.time() - start:.1f}s)")

    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
