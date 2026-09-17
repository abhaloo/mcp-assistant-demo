"""Completeness and counting helpers for coordinator execution capture."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.eval.conversation_execution_capture import IO_OPERATIONS, OPERATIONS

if TYPE_CHECKING:
    from scripts.eval.conversation_execution_capture import ExecutionCapture

_COUNTED_PHASES = frozenset({"begin", "attempt", "end"})


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def capture_is_complete(
    path: Path,
    *,
    required_operations: tuple[str, ...] = (),
    capture: ExecutionCapture | None = None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    flag = path.with_name(path.name + ".failed")
    if flag.is_file():
        reasons.append("capture write failure")
    if capture is not None and capture.failed:
        reasons.append("capture write failure")
    if capture is not None and capture.cancelled:
        reasons.append("cancelled capture")
    if not path.is_file():
        reasons.append("missing capture file")
        return False, reasons
    events = load_events(path)
    if any(e.get("phase") == "cancelled" for e in events):
        reasons.append("cancelled capture")
    if capture is not None and capture.failed:
        return False, reasons
    if not events and required_operations:
        reasons.append("empty capture")
        return False, reasons
    counted = [e for e in events if e.get("phase") in _COUNTED_PHASES]
    sequences = [int(e.get("sequence") or 0) for e in events]
    if sequences != list(range(1, len(sequences) + 1)):
        reasons.append("sequence gap")
    by_op: dict[str, list[str]] = {}
    for event in counted:
        if event.get("worker_id") is None:
            reasons.append("missing worker capture")
            break
        by_op.setdefault(str(event.get("operation")), []).append(str(event.get("phase")))
    for op, phases in by_op.items():
        begins = phases.count("begin")
        attempts = phases.count("attempt")
        ends = phases.count("end")
        if begins != attempts or attempts != ends:
            reasons.append(f"missing begin/end for {op}")
    for op in required_operations:
        if op not in by_op:
            reasons.append(f"absent operation coverage: {op}")
    return (len(reasons) == 0), reasons


def count_attempts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {op: 0 for op in OPERATIONS}
    for op in IO_OPERATIONS:
        counts[op] = 0
    for event in events:
        if event.get("phase") != "attempt":
            continue
        op = str(event.get("operation"))
        counts[op] = counts.get(op, 0) + 1
    return counts
