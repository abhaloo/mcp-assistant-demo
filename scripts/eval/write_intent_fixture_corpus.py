"""Write the intent/evidence pack's fixture documents as an ingestible corpus.

Each fixture document becomes ``<out>/<tier>/<source_id>.md`` so the ordinary
ingest reads the tier from the folder name. The output is eval data for an
eval environment, never the product corpus.

Usage:
    python scripts/eval/write_intent_fixture_corpus.py --fixtures <json> --out <dir>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def write_fixture_corpus(fixtures: dict, out_dir: Path) -> list[Path]:
    documents = fixtures.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("fixtures need a 'documents' object")
    written: list[Path] = []
    for doc in documents.values():
        source_id = str(doc["source_id"])
        tier = str(doc["tier"])
        text = str(doc["text"])
        path = out_dir / tier / f"{source_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {source_id}\n\n{text}\n", encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    written = write_fixture_corpus(fixtures, args.out)
    for path in written:
        print(path)
    print(f"wrote {len(written)} fixture documents under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
