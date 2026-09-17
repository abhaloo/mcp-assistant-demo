import json

import pytest

import app.eval.sql.diagnostics as diag
from app.eval.sql.episodic import validate_store_data
from app.eval.sql.memory import EvalSqlMemory, SqlMemoryContext, clear_memory_caches


def test_eval_memory_excludes_own_case(monkeypatch):
    seen = {}

    class _Provider:
        def for_eval_case(self, case, *, exclude_fold=frozenset()):
            seen["case_id"] = case["id"]
            return SqlMemoryContext()

    monkeypatch.setattr(diag, "_sql_memory", _Provider())
    diag.memory_for_eval_case({"id": "fin-revenue-month", "question": "revenue?"})
    assert seen["case_id"] == "fin-revenue-month"


def test_eval_memory_empty_when_disabled(monkeypatch):
    monkeypatch.setattr("app.config.settings.rules_enabled", False, raising=False)
    monkeypatch.setattr("app.config.settings.episodic_enabled", False, raising=False)
    ctx = diag.memory_for_eval_case({"id": "x", "question": "q"})
    assert ctx.rules_block == "" and ctx.episodic_block == ""


def test_eval_memory_empty_when_store_missing(monkeypatch):
    monkeypatch.setattr("app.config.settings.episodic_enabled", True, raising=False)
    monkeypatch.setattr(
        "app.config.settings.episodic_store_path", "no/such/store.json", raising=False
    )
    monkeypatch.setattr("app.config.settings.rules_enabled", False, raising=False)
    ctx = diag.memory_for_eval_case({"id": "x", "question": "q"})
    assert ctx.episodic_block == ""


def _write_definitional(tmp_path, case_id, mode, rule):
    rec = {
        "schema_version": 3,
        "status": "confirmed",
        "source": "eval",
        "mode": mode,
        "case_id": case_id,
        "failure_kind": "definitional",
        "rule": rule,
        "question": "q",
        "gold_sql": "SELECT 1",
    }
    (tmp_path / f"r_{case_id}_{mode}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_holdout_rule_leaks_to_sibling_without_fold_exclusion(tmp_path, monkeypatch):
    _write_definitional(tmp_path, "H1", "holdout", "RULE-FROM-H1")
    monkeypatch.setattr("app.config.settings.rules_enabled", True, raising=False)
    monkeypatch.setattr("app.config.settings.episodic_enabled", False, raising=False)
    monkeypatch.setattr("app.config.settings.failure_store_dir", str(tmp_path), raising=False)
    clear_memory_caches()
    # Scoring sibling H2 with only its own id excluded -> H1's rule leaks in.
    ctx = EvalSqlMemory().for_eval_case({"id": "H2", "question": "q"})
    assert "RULE-FROM-H1" in ctx.rules_block  # documents the leak path


def test_fold_exclusion_blocks_sibling_holdout_rule(tmp_path, monkeypatch):
    _write_definitional(tmp_path, "H1", "holdout", "RULE-FROM-H1")
    monkeypatch.setattr("app.config.settings.rules_enabled", True, raising=False)
    monkeypatch.setattr("app.config.settings.episodic_enabled", False, raising=False)
    monkeypatch.setattr("app.config.settings.failure_store_dir", str(tmp_path), raising=False)
    clear_memory_caches()
    ctx = EvalSqlMemory().for_eval_case(
        {"id": "H2", "question": "q"}, exclude_fold=frozenset({"H1", "H2"})
    )
    assert "RULE-FROM-H1" not in ctx.rules_block  # fold exclusion closes it


def test_episodic_rejects_eval_sourced_store_by_default():
    data = {"schema_version": 1, "source": "eval", "exemplars": [], "vectors": []}
    with pytest.raises(ValueError, match="eval-sourced store not allowed"):
        validate_store_data(data)
