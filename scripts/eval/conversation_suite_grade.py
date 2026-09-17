"""Grade suite turn_invocation_report checks against a joined capture report."""

from __future__ import annotations

from typing import Any

UNPROVEN = "UNPROVEN"
GRADED_CHECK_KEYS = frozenset(
    {
        "sql_executions",
        "authorized_restores",
        "coordinator_min",
        "reject_before_prohibited_call",
        "stop_reason",
        "actions",
        "one_complete_per_admitted",
        "missing_complete_unproven",
        "source_exchange_ids_min",
        "source_matches_persisted",
        "cancelled",
        "max_bq_sets",
        "first_two_work_steps",
        "work_steps",
        "outcome_type",
        "answer_modes",
        "replacement_exchanges",
        "no_new_call_after_cancel",
    }
)


def _actions(report: dict[str, Any]) -> dict[str, list[Any]]:
    raw = report.get("actions") or {}
    return {
        "admitted": list(raw.get("admitted") or []),
        "completed": list(raw.get("completed") or []),
        "rejected": list(raw.get("rejected") or []),
    }


def _count(report: dict[str, Any], op: str) -> int:
    return int((report.get("counts") or {}).get(op, 0))


def grade_turn_invocation_check(report: dict[str, Any], check: dict[str, Any]) -> list[str]:
    if check.get("type") != "turn_invocation_report":
        return []
    failures: list[str] = []
    for key in check:
        if key != "type" and key not in GRADED_CHECK_KEYS:
            failures.append(f"ungraded:{key}")
    actions = _actions(report)
    if "sql_executions" in check and _count(report, "business_sql") != int(check["sql_executions"]):
        failures.append("sql_executions")
    if "authorized_restores" in check:
        if _count(report, "evidence_restore") != int(check["authorized_restores"]):
            failures.append("authorized_restores")
    if "coordinator_min" in check:
        if _count(report, "coordinator_decision") < int(check["coordinator_min"]):
            failures.append("coordinator_min")
    if check.get("reject_before_prohibited_call") and not actions["rejected"]:
        failures.append("reject_before_prohibited_call")
    if "stop_reason" in check:
        if check["stop_reason"] not in (report.get("stop_reasons") or []):
            failures.append("stop_reason")
    spec = check.get("actions") or {}
    for key, field in (
        ("admitted_min", "admitted"),
        ("completed_min", "completed"),
        ("rejected_min", "rejected"),
    ):
        if spec.get(key) is not None and len(actions[field]) < int(spec[key]):
            failures.append(f"actions.{field}")
    if check.get("one_complete_per_admitted"):
        missing = any("complete" in r.lower() for r in report.get("unproven_reasons") or [])
        if report.get("status") == UNPROVEN and missing:
            failures.append("one_complete_per_admitted")
        elif len(actions["admitted"]) != len(actions["completed"]):
            failures.append("one_complete_per_admitted")
    if check.get("missing_complete_unproven"):
        if len(actions["admitted"]) > len(actions["completed"]):
            missing = any("complete" in r.lower() for r in report.get("unproven_reasons") or [])
            if report.get("status") != UNPROVEN or not missing:
                failures.append("missing_complete_unproven")
    sources = list(report.get("source_exchange_ids") or [])
    persisted = list(report.get("persisted_exchange_ids") or [])
    if "source_exchange_ids_min" in check and len(sources) < int(check["source_exchange_ids_min"]):
        failures.append("source_exchange_ids")
    if check.get("source_matches_persisted"):
        if not sources or not set(sources).issubset(set(persisted)):
            failures.append("source_matches_persisted")
    if check.get("cancelled"):
        cancel = any("cancel" in r.lower() for r in report.get("unproven_reasons") or [])
        if not cancel:
            failures.append("cancelled")
    if check.get("no_new_call_after_cancel") and report.get("new_call_after_cancel"):
        failures.append("no_new_call_after_cancel")
    if "max_bq_sets" in check and _count(report, "business_sql") > int(check["max_bq_sets"]):
        failures.append("max_bq_sets")
    if "first_two_work_steps" in check:
        expected = int(check["first_two_work_steps"])
        observed = list(report.get("work_step_counts") or [])
        first_two = observed[:2]
        if len(first_two) < 2 or any(count != expected for count in first_two):
            failures.append("first_two_work_steps")
    if check.get("work_steps") == 0 and _count(report, "business_sql") != 0:
        failures.append("work_steps")
    if "outcome_type" in check:
        observed = report.get("outcome_type")
        types = report.get("outcome_types") or []
        if observed != check["outcome_type"] and check["outcome_type"] not in types:
            failures.append("outcome_type")
    if "answer_modes" in check:
        observed_modes = report.get("answer_modes")
        if observed_modes is None or list(observed_modes) != list(check["answer_modes"]):
            failures.append("answer_modes")
    if "replacement_exchanges" in check:
        if int(report.get("replacement_exchanges") or 0) != int(check["replacement_exchanges"]):
            failures.append("replacement_exchanges")
    return failures


def grade_case_checks(case: dict[str, Any], report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for check in case.get("checks") or []:
        failures.extend(grade_turn_invocation_check(report, check))
    return failures
