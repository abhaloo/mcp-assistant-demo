"""Aggregate scored eval rows into the run summary dict."""

from __future__ import annotations


def build_summary(
    rows: list[dict],
    *,
    preflight: dict | None = None,
    repeats: int = 3,
    planner_calls: dict[str, int] | None = None,
    cases: int | None = None,
) -> dict:
    passed = sum(1 for r in rows if r["passed"])
    outcome_ok = sum(1 for r in rows if r["outcome_match"])
    by_case: dict[str, list[bool]] = {}
    phrasing_by_case: dict[str, str] = {}
    failures_by_detail: dict[str, int] = {}
    for r in rows:
        by_case.setdefault(r["case_id"], []).append(bool(r["passed"]))
        if r["case_id"] not in phrasing_by_case:
            phrasing_by_case[r["case_id"]] = r["phrasing"]
        detail = r["failure_detail"]
        if detail is not None:
            failures_by_detail[detail] = failures_by_detail.get(detail, 0) + 1
    oracle_answered = 0
    value_match = 0
    for r in rows:
        if r["actual"] == "answered" and r["literal_match"] is not None:
            oracle_answered += 1
            if r["literal_match"] is True:
                value_match += 1
    summary = {
        "preflight": preflight,
        "repeats": repeats,
        "cases": len(by_case) if cases is None else cases,
        "flaky_cases": sorted(c for c, wins in by_case.items() if 0 < sum(wins) < len(wins)),
        "stable_pass": sorted(c for c, w in by_case.items() if all(w)),
        "planner_calls": {} if planner_calls is None else planner_calls,
        "cases_run": len(rows),
        "passed": passed,
        "outcome_correct": outcome_ok,
        "accuracy": round(passed / len(rows), 3) if rows else 0.0,
        "by_phrasing": {},
        "failures_by_detail": failures_by_detail,
        "value_diagnostic": {
            "oracle_answered": oracle_answered,
            "value_match": value_match,
        },
        "phrasing_by_case": phrasing_by_case,
        "repairs": sum(r["planner_repair_count"] for r in rows),
    }
    for r in rows:
        bucket = summary["by_phrasing"].setdefault(r["phrasing"], {"n": 0, "passed": 0})
        bucket["n"] += 1
        bucket["passed"] += int(r["passed"])

    failures: dict[str, int] = {}
    for r in rows:
        if not r["passed"]:
            key = r["failure_layer"] or r["reason_code"] or r["actual"]
            failures[key] = failures.get(key, 0) + 1
    summary["failures_by_layer"] = dict(sorted(failures.items(), key=lambda kv: -kv[1]))
    planner_times = [r["planner_ms"] for r in rows if r["planner_ms"]]
    sql_times = [r["sql_ms"] for r in rows if r["sql_ms"] is not None]
    summary["latency"] = {
        "planner_ms_mean": round(sum(planner_times) / len(planner_times), 1)
        if planner_times
        else None,
        "sql_ms_mean": round(sum(sql_times) / len(sql_times), 1) if sql_times else None,
        "sql_ms_max": max(sql_times) if sql_times else None,
        "queries_executed": len(sql_times),
    }
    summary["tokens"] = {
        "prompt": sum(r["tokens_prompt"] or 0 for r in rows),
        "completion": sum(r["tokens_completion"] or 0 for r in rows),
    }
    return summary
