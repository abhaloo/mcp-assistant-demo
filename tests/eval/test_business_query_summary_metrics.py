from app.eval.business_query.summary import build_summary


def _row(
    case,
    phrasing,
    detail=None,
    passed=False,
    actual="incomplete",
    literal=None,
    repairs=0,
    failure_layer=None,
    reason_code=None,
):
    # Carries EVERY key the extracted summary body indexes with row[...]
    # (scripts/eval/business_query_eval_run.py summary assembly plus _attempt_record).
    # Fail-loud: do NOT convert those reads to .get(). Real rows always carry
    # these keys via the trace spread + _attempt_record.
    layer = failure_layer if failure_layer is not None else ("planner" if detail else None)
    return {
        "case_id": case,
        "family": "test",
        "phrasing": phrasing,
        "failure_detail": detail,
        "failure_layer": layer,
        "reason_code": reason_code,
        "detail": detail,
        "passed": passed,
        "actual": actual,
        "outcome_match": passed,
        "literal_match": literal,
        "expected": "answered",
        "receipt_valid": True,
        "planner_repair_count": repairs,
        "planner_ms": 1000.0,
        "sql_ms": None,
        "tokens_prompt": 100,
        "tokens_completion": 10,
        "tokens_reasoning": 5,
    }


def test_summary_reports_detail_layer_value_diagnostic_and_repairs():
    rows = [
        _row("bq-01", "noisy_english", detail="planner_schema_invalid", repairs=1),
        _row("bq-01", "noisy_english", detail="planner_schema_invalid"),
        _row("bq-11", "code_switched", actual="answered", literal=False),
        _row("bq-12", "code_switched", actual="answered", literal=True, passed=True, repairs=1),
    ]
    summary = build_summary(rows)
    assert summary["failures_by_detail"] == {"planner_schema_invalid": 2}
    assert summary["value_diagnostic"] == {"oracle_answered": 2, "value_match": 1}
    assert summary["phrasing_by_case"]["bq-01"] == "noisy_english"
    assert summary["repairs"] == 2
