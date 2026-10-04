"""Browser transcripts from saved SSE bodies, read the way the journeys read the wire.

The browser pass saves each turn's raw SSE body and a manifest; this script writes
the transcript, so no transcript field is typed by hand. Each body is parsed with
``ask_v2_journey_run.parse_sse``. A body with no terminal frame, or whose run id
differs from the manifest's, stops the run. A manifest row marked
``"aborted": true`` (the person pressed Stop or reloaded, so the client ended the
run) may have no terminal frame: it is written with ``outcome_type``
``no_terminal``, and its run id is checked only when its frames carry one.
A manifest row marked ``"no_request": true`` is a step that sent no question;
it is written with ``outcome_type`` ``no_request`` and its screenshot only.
Restore and continuation values are never copied.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from scripts.eval.ask_v2_journey_run import parse_sse

_ACTION_ID = re.compile(r"(?<!\w)act-\d+(?!\w)")
_LIST_KEYS = ("plain_words_keys", "schema_words_keys", "prompt_words_keys", "member_words_keys")


class TranscriptError(ValueError):
    """A saved body that cannot be a transcript: no terminal frame, or another run."""


def _tables(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for frame in frames:
        table_id = str(frame.get("table_id", ""))
        if frame.get("event_type") == "table_start":
            presentation = frame.get("presentation") or {}
            tables[table_id] = {
                "title": presentation.get("title"),
                "filters": presentation.get("applied_filters") or [],
                "columns": [c.get("label") or c.get("key") for c in frame.get("columns") or []],
            }
        elif frame.get("event_type") == "table_end" and table_id in tables:
            tables[table_id]["rows_shown"] = frame.get("row_count")
            tables[table_id]["total"] = frame.get("total_row_count")
    return list(tables.values())


def word_hits(text: str, words: list[str]) -> list[str]:
    """Every listed word and action id that appears in ``text`` as a whole token."""
    hits = [w for w in words if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text)]
    return sorted(set(hits) | set(_ACTION_ID.findall(text)))


def transcript(entry: dict[str, Any], words: list[str]) -> dict[str, Any]:
    """One case of the manifest as the wire showed it."""
    if entry.get("no_request") is True:
        # A step that sent no question (a refused input, a reload, Back) has no body.
        return {
            "case": entry["case"],
            "outcome_type": "no_request",
            "screenshot": entry.get("screenshot"),
        }
    body = Path(entry["sse"]).read_text(encoding="utf-8").replace("\r\n", "\n")
    observed = parse_sse(body)
    aborted = entry.get("aborted") is True
    if observed.outcome is None and not aborted:
        raise TranscriptError(f"{entry['case']}: the saved body has no terminal frame")
    run_ids = {f.get("run_id") for f in observed.frames if f.get("run_id")}
    if run_ids != {entry["run_id"]} and not (aborted and not run_ids):
        raise TranscriptError(f"{entry['case']}: run id {sorted(run_ids)} is not the manifest's")
    thought = "".join(
        str(f.get("delta", "")) for f in observed.frames if f.get("event_type") == "thought_delta"
    )
    terminal = observed.outcome or {}
    if observed.outcome is None:
        kind = "no_terminal"
    else:
        kind = terminal.get("outcome_type") or f"stream_error:{terminal.get('code')}"
    return {
        "case": entry["case"],
        "persona": entry.get("persona"),
        "thread": entry.get("thread"),
        "run_id": entry["run_id"],
        "outcome_type": kind,
        "reason_code": terminal.get("reason_code"),
        "trusted": terminal.get("trusted"),
        "follow_ups": [
            {"label": f.get("label"), "prompt": f.get("prompt")}
            for f in terminal.get("follow_ups") or []
        ],
        "unanswered_part": terminal.get("unanswered_part"),
        "answer_text": observed.answer_text,
        "tables": _tables(observed.frames),
        "thought_text": thought,
        "word_hits": {
            "thought": word_hits(thought, words),
            "answer": word_hits(observed.answer_text, words),
        },
        "screenshot": entry.get("screenshot"),
        "screenshot_stale": entry.get("screenshot_run_id") not in (None, entry["run_id"]),
    }


def _words(path: str | None) -> list[str]:
    if path is None:
        return []
    lists = json.loads(Path(path).read_text(encoding="utf-8"))
    return sorted({word for key in _LIST_KEYS for word in lists.get(key, [])})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--word-lists")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    words = _words(args.word_lists)
    try:
        cases = [transcript(entry, words) for entry in manifest]
    except TranscriptError as error:
        print(str(error), file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
