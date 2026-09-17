import json

import pytest

from app.eval.failure_store import (
    failure_record,
    find_existing_record,
    has_human_triage,
    merge_candidate_record,
    production_path_for,
    should_capture,
    write_failures,
)


def _cs(cid, pass_rate, *, degenerate=False, valid=1.0, flaky=False):
    return {
        "case_id": cid,
        "pass_rate": pass_rate,
        "valid_sql_rate": valid,
        "flaky": flaky,
        "degenerate_candidate": degenerate,
    }


def _run(cid, *, match, sql="SELECT 1", err=None, dep="gpt-4o-mini"):
    return {
        "case_id": cid,
        "match": match,
        "generated_sql_scrubbed": sql,
        "error_category": err,
        "reason": "ok" if match else "mismatch",
        "chat_deployment": dep,
    }


def test_should_capture_only_low_scoring_non_degenerate():
    assert should_capture(_cs("a", 0.4)) is True
    assert should_capture(_cs("b", 1.0)) is False
    assert should_capture(_cs("c", 0.0, degenerate=True)) is False


def test_failure_record_scrubs_pii_in_question():
    case = {
        "id": "c1",
        "role": "finance",
        "question": "how much does john.doe@example.com owe?",
        "gold_sql": "SELECT 1",
    }
    rec = failure_record(
        _cs("c1", 0.5), case, [_run("c1", match=False)], run_id="r", mode="dev", source="production"
    )
    assert "john.doe@example.com" not in rec["question"]
    assert rec["source"] == "production"
    assert rec["status"] == "candidate"


def test_failure_record_rejects_unknown_source():
    case = {"id": "c1", "role": "finance", "question": "q", "gold_sql": "SELECT 1"}
    with pytest.raises(ValueError):
        failure_record(_cs("c1", 0.5), case, [], run_id="r", mode="dev", source="bogus")


def test_failure_record_picks_a_representative_miss():
    case = {"id": "c1", "role": "finance", "question": "q", "gold_sql": "SELECT 1"}
    runs = [
        _run("c1", match=True, sql="SELECT good"),
        _run("c1", match=False, sql="SELECT bad"),
    ]
    rec = failure_record(_cs("c1", 0.5), case, runs, run_id="r", mode="dev", source="eval")
    assert rec["agent_sql_scrubbed"] == "SELECT bad"
    assert rec["lever"] is None and rec["root_cause"] is None
    assert rec["failure_kind"] is None


def test_write_failures_names_files_skips_passes_and_tags_source(tmp_path):
    cases = [
        {"id": "miss-1", "role": "finance", "question": "q1", "gold_sql": "SELECT 1"},
        {"id": "pass-1", "role": "finance", "question": "q2", "gold_sql": "SELECT 2"},
    ]
    summary = {
        "cases": [_cs("miss-1", 0.2), _cs("pass-1", 1.0)],
        "runs": [_run("miss-1", match=False), _run("pass-1", match=True)],
    }
    paths = write_failures(summary, cases, out_dir=tmp_path, run_id="r", mode="dev", source="eval")
    assert len(paths) == 1 and paths[0].name == "r_miss-1_dev.json"
    assert json.loads(paths[0].read_text(encoding="utf-8"))["source"] == "eval"


def test_write_failures_never_clobbers_confirmed(tmp_path):
    p = tmp_path / "r_miss-1_dev.json"
    p.write_text(json.dumps({"status": "confirmed", "lever": "prompt"}), encoding="utf-8")
    cases = [{"id": "miss-1", "role": "finance", "question": "q1", "gold_sql": "SELECT 1"}]
    summary = {"cases": [_cs("miss-1", 0.2)], "runs": [_run("miss-1", match=False)]}
    write_failures(summary, cases, out_dir=tmp_path, run_id="r", mode="dev", source="eval")
    assert json.loads(p.read_text(encoding="utf-8"))["status"] == "confirmed"


def test_has_human_triage_detects_root_cause():
    assert has_human_triage({"root_cause": "wrong join"}) is True
    assert has_human_triage({"failure_kind": None, "rule": None}) is False


def test_merge_candidate_record_preserves_human_fields():
    existing = {
        "status": "candidate",
        "root_cause": "human note",
        "pass_rate": 0.5,
        "question": "old q",
    }
    incoming = {
        "status": "candidate",
        "root_cause": "machine guess",
        "pass_rate": 0.0,
        "question": "new q",
    }
    merged = merge_candidate_record(existing, incoming)
    assert merged["root_cause"] == "human note"
    assert merged["question"] == "new q"


def test_production_dedupe_by_case_id(tmp_path):
    cases = [{"id": "prod-abc", "role": "sales", "question": "q", "gold_sql": None}]
    summary = {
        "cases": [_cs("prod-abc", 0.0, valid=None)],
        "runs": [
            {
                "case_id": "prod-abc",
                "match": False,
                "generated_sql_scrubbed": None,
                "error_category": None,
                "reason": "downvoted",
                "chat_deployment": None,
            }
        ],
    }
    p1 = write_failures(
        summary, cases, out_dir=tmp_path, run_id="harvest-1", mode="prod", source="production"
    )
    p2 = write_failures(
        summary, cases, out_dir=tmp_path, run_id="harvest-2", mode="prod", source="production"
    )
    assert len(p1) == 1 and len(p2) == 1
    assert p1[0] == p2[0] == production_path_for(tmp_path, case_id="prod-abc", mode="prod")
    assert find_existing_record(tmp_path, case_id="prod-abc", mode="prod", source="production")


def test_candidate_with_human_root_cause_not_overwritten(tmp_path):
    stable = production_path_for(tmp_path, case_id="prod-x", mode="prod")
    stable.write_text(
        json.dumps({"status": "candidate", "case_id": "prod-x", "root_cause": "human edit"}),
        encoding="utf-8",
    )
    cases = [{"id": "prod-x", "role": "sales", "question": "q", "gold_sql": None}]
    summary = {
        "cases": [_cs("prod-x", 0.0, valid=None)],
        "runs": [
            {
                "case_id": "prod-x",
                "match": False,
                "generated_sql_scrubbed": None,
                "error_category": None,
                "reason": "downvoted",
                "chat_deployment": None,
            }
        ],
    }
    write_failures(summary, cases, out_dir=tmp_path, run_id="h2", mode="prod", source="production")
    rec = json.loads(stable.read_text(encoding="utf-8"))
    assert rec["root_cause"] == "human edit"
