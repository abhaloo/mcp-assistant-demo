"""Map the intent/evidence pack onto ``CoordinatorCase`` rows for one live run.

Each turn becomes one case. The case carries the pack's principal so the
runner presents the right persona, and its expected sources are the published
identities of the fixture documents the turn must cite, when a published map
is given. Without that map the sources stay empty and the scorer reports the
axis as unproven rather than failing it.

Usage:
    python scripts/eval/map_intent_pack.py --pack <jsonl> --fixtures <json> --out <jsonl>
        [--published-sources <json: source_id -> [published ids]>]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from pathlib import Path

STRATUM = {
    "explanation": "explanation",
    "fresh_bq": "fresh_bq",
    "general": "general",
    "documents": "documents_mixed",
    "mixed": "documents_mixed",
    "ambiguity": "ambiguity_regeneration_focus",
    "access": "failure_access_budget",
}

TOOLS = frozenset({"query_business", "search_documents", "explain_sources"})


def _history_lines(fixtures: dict, history_key: str | None, case_id: str) -> list[dict]:
    histories = fixtures.get("histories") or {}
    lines: list[dict] = []
    for index, item in enumerate(histories.get(history_key) or []):
        lines.append(
            {
                "exchange_id": item.get("source_id") or f"{case_id}-h{index}",
                "user_text": item["question"],
            }
        )
    # The coordinator reads the last eight user turns; the case carries no more.
    return lines[:8]


def _expected_sources(refs: Iterable[str], published: dict[str, list[str]] | None) -> list[str]:
    if not published:
        return []
    seen: list[str] = []
    for ref in refs:
        for identity in published.get(ref, ()):
            if identity not in seen:
                seen.append(identity)
    return seen


def map_pack(
    pack_rows: Iterable[dict],
    fixtures: dict,
    *,
    published_sources: dict[str, list[str]] | None,
) -> list[dict]:
    rows: list[dict] = []
    for pack in pack_rows:
        setup = pack["setup"]
        case_id = pack["case_id"]
        if pack["category"] not in STRATUM:
            raise ValueError(f"{case_id}: unknown pack category {pack['category']!r}")
        history = _history_lines(fixtures, setup.get("history"), case_id)
        for turn_index, turn in enumerate(pack["turns"]):
            if turn.get("operation") == "reload" or not str(turn.get("text") or "").strip():
                continue
            expected = turn["expected"]
            sequences = expected.get("allowed_action_sequences") or []
            if not sequences:
                raise ValueError(
                    f"{case_id}: turn {turn_index + 1} has no allowed_action_sequences"
                )
            required, alternatives = sequences[0], sequences[1:]
            used = set(required)
            for alternative in alternatives:
                used.update(alternative)
            turn_case_id = case_id if turn_index == 0 else f"{case_id}-t{turn_index + 1}"
            rows.append(
                {
                    "case_id": turn_case_id,
                    "stratum": STRATUM[pack["category"]],
                    "context": {
                        "turn_id": f"turn-{turn_case_id}",
                        "question": turn["text"],
                        "history": history,
                        "candidates": [],
                        "focus": {
                            "status": "none",
                            "source_positions": [],
                            "subject_question": None,
                        },
                        "catalog_descriptions": [],
                        "capabilities": [],
                    },
                    "expected_sources": _expected_sources(
                        expected.get("required_evidence_refs") or (), published_sources
                    ),
                    "required_actions": required,
                    "allowed_alternatives": alternatives,
                    "forbidden_calls": sorted(TOOLS - used),
                    "expected_question_origin": expected.get("expected_question_origin"),
                    "answer_oracle": None,
                    "principal": setup.get("principal"),
                    "provenance": f"{pack.get('fixture_file', 'pack')}:{case_id}",
                }
            )
    return rows


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--published-sources", type=Path, default=None)
    args = parser.parse_args()
    fixtures = json.loads(args.fixtures.read_text(encoding="utf-8"))
    published = (
        json.loads(args.published_sources.read_text(encoding="utf-8"))
        if args.published_sources
        else None
    )
    rows = map_pack(_read_jsonl(args.pack), fixtures, published_sources=published)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=True) for r in rows) + "\n", "utf-8")
    print(f"wrote {len(rows)} cases to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
