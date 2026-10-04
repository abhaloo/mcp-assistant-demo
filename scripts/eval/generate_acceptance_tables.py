"""Generate acceptance tables from raw evaluation and run files."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

_BAD_PREFIXES = ("you could", "would you like", "i can")


def _read_report(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def _terminal_outcome(item: dict[str, Any]) -> dict[str, Any]:
    reply = item.get("reply")
    if isinstance(reply, dict) and reply.get("outcome"):
        outcome = reply["outcome"]
        return outcome if isinstance(outcome, dict) else {}
    obs = item.get("observation")
    if isinstance(obs, dict) and obs.get("outcome"):
        outcome = obs["outcome"]
        return outcome if isinstance(outcome, dict) else {}
    if isinstance(obs, dict):
        return obs
    return {}


def _has_valid_follow_up(outcome: dict[str, Any]) -> bool:
    follow_ups = outcome.get("follow_ups") or []
    for fu in follow_ups:
        if not isinstance(fu, dict):
            continue
        label = str(fu.get("label", "")).strip().lower()
        prompt = str(fu.get("prompt", "")).strip().lower()
        if any(label.startswith(p) for p in _BAD_PREFIXES):
            continue
        if any(prompt.startswith(p) for p in _BAD_PREFIXES):
            continue
        return True
    return False


def _compute_ac6(reports: list[Path]) -> dict[str, Any]:
    explained_failures = 0
    with_valid_follow_up = 0
    minimum = 10
    for report_path in reports:
        for item in _read_report(report_path):
            outcome = _terminal_outcome(item)
            if (
                outcome.get("event_type") == "turn_outcome"
                and outcome.get("outcome_type") == "unsupported"
            ):
                explained_failures += 1
                if _has_valid_follow_up(outcome):
                    with_valid_follow_up += 1

    rate = round(with_valid_follow_up / explained_failures, 4) if explained_failures > 0 else None
    measured = bool(explained_failures >= minimum)
    return {
        "explained_failures": explained_failures,
        "with_valid_follow_up": with_valid_follow_up,
        "rate": rate,
        "minimum": minimum,
        "measured": measured,
    }


def _compute_journeys(reports: list[Path]) -> tuple[dict[str, Any], list[str]]:
    journeys: dict[str, dict[str, int]] = {}
    run_ids: list[str] = []
    for report_path in reports:
        for item in _read_report(report_path):
            case_id = str(item.get("id", ""))
            run_id = item.get("run_id")
            if run_id is not None and str(run_id) not in run_ids:
                run_ids.append(str(run_id))
            if case_id not in journeys:
                journeys[case_id] = {"runs": 0, "passed": 0}
            journeys[case_id]["runs"] += 1
            if not item.get("mismatches"):
                journeys[case_id]["passed"] += 1
    return journeys, run_ids


def _read_records(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return {
            row["correlation_id"]: row for row in reader if row.get("correlation_id") is not None
        }


def _compute_ac11(reports: list[Path], records: dict[str, dict[str, str]]) -> dict[str, Any]:
    sample_ids: list[str] = []
    for report_path in reports:
        for item in _read_report(report_path):
            run_id = item.get("run_id")
            if run_id is not None and str(run_id) not in sample_ids:
                sample_ids.append(str(run_id))

    ac11: dict[str, Any] = {}
    for sid in sample_ids:
        rec = records.get(sid, {})
        disposition = rec.get("resolver_disposition", "")
        trace = rec.get("bq_trace_present") == "t"
        cost = rec.get("estimated_usd", "")
        ok = disposition in ("unsupported", "denied") and trace and bool(cost.strip())
        ac11[sid] = {
            "disposition": disposition,
            "trace": trace,
            "estimated_usd": cost,
            "ok": ok,
        }
    ac11["all_ok"] = bool(sample_ids and all(ac11[sid]["ok"] for sid in sample_ids))
    return ac11


def _compute_cq(reports: list[Path]) -> dict[str, int]:
    runs = 0
    with_notice = 0
    for report_path in reports:
        for item in _read_report(report_path):
            runs += 1
            outcome = _terminal_outcome(item)
            if outcome.get("unanswered_part"):
                with_notice += 1
    return {"runs": runs, "with_notice": with_notice}


def _compute_bench(base_path: Path, merged_path: Path) -> dict[str, Any]:
    base_rows = [
        json.loads(line)
        for line in base_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    merged_rows = [
        json.loads(line)
        for line in merged_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    base_map = {r["id"]: r.get("classification") for r in base_rows}
    merged_map = {r["id"]: r.get("classification") for r in merged_rows}
    shared_ids = [cid for cid in base_map if cid in merged_map]
    regressions = [
        cid for cid in shared_ids if base_map[cid] == "pass" and merged_map[cid] != "pass"
    ]
    changed = {
        cid: [base_map[cid], merged_map[cid]]
        for cid in shared_ids
        if base_map[cid] != merged_map[cid]
    }
    merged_only = [cid for cid in merged_map if cid not in base_map]
    return {
        "shared": len(shared_ids),
        "changed": changed,
        "regressions": regressions,
        "merged_only": merged_only,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journeys", nargs="*", default=[])
    parser.add_argument("--ac6", nargs="*", default=[])
    parser.add_argument("--spend-gate", nargs="*", default=[])
    parser.add_argument("--cq", nargs="*", default=[])
    parser.add_argument("--records", required=True)
    parser.add_argument("--bench-base", required=True)
    parser.add_argument("--bench-merged", required=True)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    records_path = Path(args.records)
    records = _read_records(records_path)

    journey_paths = [Path(p) for p in args.journeys]
    journeys, journey_run_ids = _compute_journeys(journey_paths)

    missing_run_ids = [rid for rid in journey_run_ids if rid not in records]
    if missing_run_ids:
        sys.stderr.write(f"Missing query records for run ids: {', '.join(missing_run_ids)}\n")
        return 1

    spend = {rid: records[rid].get("estimated_usd", "") for rid in journey_run_ids}
    ac6 = _compute_ac6([Path(p) for p in args.ac6])
    ac11 = _compute_ac11([Path(p) for p in args.spend_gate], records)
    cq = _compute_cq([Path(p) for p in args.cq])
    bench = _compute_bench(Path(args.bench_base), Path(args.bench_merged))

    tables = {
        "ac6": ac6,
        "journeys": journeys,
        "spend": spend,
        "ac11": ac11,
        "cq": cq,
        "bench": bench,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(tables, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
