"""Named calendar-day intent tests (Phase E1).

Oracle: evals/experiments/sql-post-canary-phase-e-contract.json hand fixtures —
never the implementation under test, never gold SQL.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake import FakeListLLM
from sqlalchemy import create_engine, text

from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer
from app.eval.sql.agent import agent as sql_chain
from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.date_windows import (
    classify_named_day_intent,
    named_day_rules_block,
)

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "evals"
    / "experiments"
    / "sql-post-canary-phase-e-contract.json"
)
_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
_E1_FIXTURES: list[tuple[str, str]] = [
    (entry["question"], entry["expected_date_semantics"])
    for entry in _CONTRACT["sub_slices"]["E1_on_day_vs_from_day"]["hand_fixtures"]
]

_SEMANTICS_TO_INTENT = {
    "on_day": "on_day",
    "from_day": "from_day",
    "from_day_or_clarify": "clarify",
    "clarify": "clarify",
}


@pytest.mark.parametrize("question,expected_semantics", _E1_FIXTURES)
def test_classify_named_day_intent_matches_contract_oracle(
    question: str, expected_semantics: str
) -> None:
    expected_intent = _SEMANTICS_TO_INTENT[expected_semantics]
    assert classify_named_day_intent(question) == expected_intent


@pytest.mark.parametrize(
    "question",
    [
        "revenue this month",
        "how many orders today",
        "what was revenue last week",
    ],
)
def test_relative_dates_without_named_day_return_none(question: str) -> None:
    assert classify_named_day_intent(question) == "none"


def test_named_day_rules_block_nonempty_for_classified_intents() -> None:
    assert "single day" in named_day_rules_block("on_day").lower()
    assert ">=" in named_day_rules_block("from_day")
    assert "CLARIFY:" in named_day_rules_block("clarify")
    assert named_day_rules_block("none") == ""


def test_dated_prefix_injects_named_day_rule_when_question_has_on_day_cue(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "named_date.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.commit()

    captured: dict[str, str] = {}

    def capture_graph(db, llm, system_content, **kwargs):
        captured["system_content"] = system_content
        return MagicMock()

    monkeypatch.setattr(sql_chain, "build_sql_graph", capture_graph)
    monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: FakeListLLM(responses=["ok"]))
    monkeypatch.setattr("app.config.settings.redaction_enabled", False)

    anon = SqlAnonymizer("q", role="admin")
    db = AnonymizingSQLDatabase(engine, anonymizer=anon)
    from datetime import date

    sql_chain.get_sql_chain(
        ["all"],
        "admin",
        db=db,
        anonymizer=anon,
        chat_deployment="gpt-4o-mini",
        today=date(2026, 6, 16),
        question="what was the revenue on June 9",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    content = captured["system_content"]
    assert "NAMED CALENDAR DAY (on-day" in content
    assert "takes precedence over relative-date recency" in content
    assert "prefer the most recent period" in content.lower()


def test_dated_prefix_omits_named_day_rule_without_named_day(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "named_date2.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.commit()

    captured: dict[str, str] = {}

    def capture_graph(db, llm, system_content, **kwargs):
        captured["system_content"] = system_content
        return MagicMock()

    monkeypatch.setattr(sql_chain, "build_sql_graph", capture_graph)
    monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: FakeListLLM(responses=["ok"]))
    monkeypatch.setattr("app.config.settings.redaction_enabled", False)

    anon = SqlAnonymizer("q", role="admin")
    db = AnonymizingSQLDatabase(engine, anonymizer=anon)
    from datetime import date

    sql_chain.get_sql_chain(
        ["all"],
        "admin",
        db=db,
        anonymizer=anon,
        chat_deployment="gpt-4o-mini",
        today=date(2026, 6, 16),
        question="revenue this month",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    content = captured["system_content"]
    assert "NAMED CALENDAR DAY" not in content
