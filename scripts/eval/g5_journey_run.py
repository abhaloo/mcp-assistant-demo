"""Drive POST /api/ask for the G5 invoice journey and compare each turn."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--journey", required=True)
    parser.add_argument("--out", default=None)
    return parser.parse_args(argv)


def load_journey(path: Path | str) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def compare_turn(expected: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if expected.get("no_clarification") and observed.get("clarification"):
        mismatches.append("clarification")
    want_id = expected.get("row_identity") or {}
    got_id = observed.get("row_identity") or {}
    for key, value in want_id.items():
        if got_id.get(key) != value:
            mismatches.append(f"row_identity.{key}")
    observed_columns = observed.get("columns_present") or ()
    mismatches.extend(
        f"missing_column:{column}"
        for column in expected.get("columns_present") or ()
        if column not in observed_columns
    )
    want_changes = list((expected.get("receipt") or {}).get("changes") or ())
    got_changes = list((observed.get("receipt") or {}).get("changes") or ())
    if want_changes != got_changes:
        mismatches.append("receipt.changes")
    return mismatches


def _post_ask(base_url: str, question: str, thread_id: str) -> dict[str, Any]:
    payload = {
        "protocol_version": "2",
        "operation": "new_question",
        "run_id": uuid.uuid4().hex,
        "thread_id": thread_id,
        "question": question,
        "idempotency_key": uuid.uuid4().hex,
        "response_policy": "allow_partial",
    }
    response = httpx.post(f"{base_url.rstrip('/')}/api/ask", json=payload, timeout=120.0)
    response.raise_for_status()
    return response.json()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    turns = load_journey(args.journey)
    thread_id = uuid.uuid4().hex
    rows: list[dict[str, Any]] = []
    for turn in turns:
        try:
            body = _post_ask(args.base_url, turn["question"], thread_id)
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            rows.append({"id": turn["id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        thread_id = str(body.get("thread_id") or thread_id)
        observed = {
            "row_identity": (body.get("row_identity") or {}),
            "columns_present": list(body.get("columns_present") or []),
            "receipt": body.get("receipt") or {},
            "clarification": bool(body.get("clarification") or body.get("needs_clarification")),
        }
        rows.append(
            {
                "id": turn["id"],
                "mismatches": compare_turn(turn.get("expected") or {}, observed),
                "observed": observed,
            }
        )
    out = Path(args.out) if args.out else REPO_ROOT / "evals" / "g5_invoice_journey" / "runs.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return 0 if all(not row.get("mismatches") and "error" not in row for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
