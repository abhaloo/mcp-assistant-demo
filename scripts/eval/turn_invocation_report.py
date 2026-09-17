"""Join durable invocation ledger rows with a complete execution capture."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.telemetry.invocation_payload import get_recorded_evidence
from scripts.eval.conversation_capture_fields import lineage_from_events
from scripts.eval.conversation_execution_capture import (
    IO_OPERATIONS,
    OPERATIONS,
    capture_is_complete,
    count_attempts,
    load_events,
)

UNPROVEN = "UNPROVEN"
COMPLETE = "COMPLETE"
ZERO_CALL_OPS = (
    "planner",
    "business_sql",
    "document_retrieval",
    "record_rehydration",
    "condense",
)
CONTROL_OPS = (
    "planner",
    "business_sql",
    "document_retrieval",
    "record_rehydration",
    "condense",
    "answer_generation",
    "coordinator_decision",
)


def _purpose_of(record: Any) -> str | None:
    purpose = getattr(record, "purpose", None)
    if purpose is not None:
        return str(purpose)
    if isinstance(record, dict) and "purpose" in record:
        return str(record["purpose"])
    return None


def _is_sql_record(record: Any) -> bool:
    if type(record).__name__ == "SqlExecutionRecord":
        return True
    statement = getattr(record, "statement", None)
    return isinstance(statement, str) and bool(statement)


def _ledger_view(run_id: str) -> tuple[list[str], int]:
    records = get_recorded_evidence(run_id)
    purposes = [p for p in (_purpose_of(r) for r in records) if p]
    sql_rows = sum(1 for r in records if _is_sql_record(r))
    return purposes, sql_rows


def _label(event: dict[str, Any]) -> Any:
    return event.get("action") or event.get("step_id") or event.get("operation")


def _action_fields(events: list[dict[str, Any]]) -> dict[str, list[Any]]:
    admitted: list[Any] = []
    completed: list[Any] = []
    rejected: list[Any] = []
    step_ids: list[Any] = []
    stop_reasons: list[Any] = []
    sources: list[Any] = []
    persisted: list[Any] = []
    for event in events:
        if event.get("admitted") is True:
            admitted.append(_label(event))
        if event.get("admitted") is False:
            rejected.append(_label(event))
        if event.get("completed") is True:
            completed.append(_label(event))
        if event.get("step_id"):
            step_ids.append(event["step_id"])
        if event.get("stop_reason"):
            stop_reasons.append(event["stop_reason"])
        for item in event.get("source_exchange_ids") or []:
            sources.append(item)
        for item in event.get("persisted_exchange_ids") or []:
            persisted.append(item)
    return {
        "admitted": admitted,
        "completed": completed,
        "rejected": rejected,
        "step_ids": step_ids,
        "stop_reasons": stop_reasons,
        "source_exchange_ids": sources,
        "persisted_exchange_ids": persisted,
    }


def _events_for_run(events: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    return [e for e in events if e.get("correlation_id") == run_id]


def _missing_complete_reasons(events: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    pending: str | None = None
    for event in events:
        if event.get("admitted") is True:
            if pending is not None:
                reasons.append("missing complete before next model step")
            pending = str(event.get("step_id") or event.get("action") or "")
        if event.get("completed") is True:
            done = str(event.get("step_id") or event.get("action") or "")
            if pending is not None and done == pending:
                pending = None
        if (
            event.get("operation") == "coordinator_decision"
            and event.get("phase") == "begin"
            and pending is not None
        ):
            reasons.append("missing complete before next model step")
    if pending is not None:
        reasons.append("missing action completion")
    return reasons


def _observed_control_ops(events: list[dict[str, Any]]) -> set[str]:
    by_op: dict[str, list[str]] = {}
    for event in events:
        if event.get("phase") not in {"begin", "end"}:
            continue
        by_op.setdefault(str(event.get("operation")), []).append(str(event.get("phase")))
    return {op for op, phases in by_op.items() if phases.count("begin") and phases.count("end")}


def _unproven_shell(run_id: str, purposes: list[str], sql_rows: int, reasons: list[str]) -> dict:
    return {
        "status": UNPROVEN,
        "exit_code": 2,
        "run_id": run_id,
        "counts": {op: 0 for op in OPERATIONS},
        "ledger_purposes": purposes,
        "ledger_sql_rows": sql_rows,
        "actions": {"admitted": [], "completed": [], "rejected": []},
        "step_ids": [],
        "stop_reasons": [],
        "source_exchange_ids": [],
        "persisted_exchange_ids": [],
        "outcome_type": None,
        "answer_modes": None,
        "replacement_exchanges": 0,
        "work_step_counts": [],
        "new_call_after_cancel": False,
        "snapshot_io": 0,
        "transcript_io": 0,
        "unproven_reasons": reasons,
        "io_operations": list(IO_OPERATIONS),
    }


def build_report(
    run_id: str,
    capture_path: Path,
    *,
    expected_binding_hash: str | None = None,
) -> dict[str, Any]:
    purposes, sql_rows = _ledger_view(run_id)
    reasons: list[str] = []
    if not capture_path.is_file():
        reasons.append("missing capture file")
        return _unproven_shell(run_id, purposes, sql_rows, reasons)

    events = load_events(capture_path)
    run_events = _events_for_run(events, run_id)
    if not run_events:
        reasons.append("no capture events for run_id")
        return _unproven_shell(run_id, purposes, sql_rows, reasons)

    reasons.extend(capture_is_complete(capture_path)[1])
    reasons.extend(_missing_complete_reasons(run_events))
    counts = count_attempts(run_events)
    actions = _action_fields(run_events)
    lineage = lineage_from_events(run_events)
    session_ids = {e.get("session_id") for e in run_events}
    builds = {e.get("build_identity") for e in run_events}
    control_events = [
        e
        for e in events
        if e.get("correlation_id") != run_id
        and e.get("session_id") in session_ids
        and e.get("build_identity") in builds
    ]
    observed = _observed_control_ops(control_events if control_events else events)
    hashes = [str(e.get("binding_hash")) for e in events if e.get("binding_hash")]
    if expected_binding_hash:
        captured_hash = hashes[0] if hashes else None
        if captured_hash != expected_binding_hash and expected_binding_hash not in hashes:
            reasons.append("mismatched binding hash")
    zero_claimed = all(counts.get(op, 0) == 0 for op in ZERO_CALL_OPS)
    if zero_claimed:
        missing = [op for op in CONTROL_OPS if op not in observed]
        if missing:
            reasons.append("zero-call claim without same-build observed controls")
    status = COMPLETE if not reasons else UNPROVEN
    return {
        "status": status,
        "exit_code": 0 if status == COMPLETE else 2,
        "run_id": run_id,
        "counts": {op: counts.get(op, 0) for op in OPERATIONS},
        "ledger_purposes": purposes,
        "ledger_sql_rows": sql_rows,
        "actions": {
            "admitted": actions["admitted"],
            "completed": actions["completed"],
            "rejected": actions["rejected"],
        },
        "step_ids": actions["step_ids"],
        "stop_reasons": actions["stop_reasons"],
        "source_exchange_ids": lineage["source_exchange_ids"],
        "persisted_exchange_ids": lineage["persisted_exchange_ids"],
        "outcome_type": lineage["outcome_type"],
        "answer_modes": lineage["answer_modes"],
        "replacement_exchanges": lineage["replacement_exchanges"],
        "work_step_counts": lineage["work_step_counts"],
        "new_call_after_cancel": lineage["new_call_after_cancel"],
        "snapshot_io": counts.get("snapshot_io", 0),
        "transcript_io": counts.get("transcript_io", 0),
        "unproven_reasons": reasons,
        "io_operations": list(IO_OPERATIONS),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join invocation ledger evidence with a complete execution capture."
    )
    parser.add_argument("run_id")
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--binding-hash", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(
        args.run_id,
        args.capture,
        expected_binding_hash=args.binding_hash or None,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(report["status"])
        if report["unproven_reasons"]:
            for reason in report["unproven_reasons"]:
                print(reason)
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
