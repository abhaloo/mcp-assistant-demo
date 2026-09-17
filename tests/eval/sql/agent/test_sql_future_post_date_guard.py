"""Future post_date guard tests (Phase E2).

Oracle: evals/experiments/sql-post-canary-phase-e-contract.json hand fixtures —
never the implementation under test, never gold SQL.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake import FakeListLLM
from sqlalchemy import create_engine, text

from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer
from app.eval.sql.agent import agent as sql_chain
from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.date_windows import (
    future_date_policy,
    future_date_rules_block,
)

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "evals"
    / "experiments"
    / "sql-post-canary-phase-e-contract.json"
)
_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
_TODAY = date(2026, 6, 16)

_FILTER_TO_POLICY = {
    "exclude post_date > today unless month bucket explicitly includes future": "exclude_future",
    "future month allowed — user explicitly named future period": "allow_future",
    "post_date <= today": "exclude_future",
}

_E2_FIXTURES: list[tuple[str, str]] = [
    (entry["question"], entry["expected_filter"])
    for entry in _CONTRACT["sub_slices"]["E2_future_post_date_guard"]["hand_fixtures"]
]


@pytest.mark.parametrize("question,expected_filter", _E2_FIXTURES)
def test_future_date_policy_matches_contract_oracle(question: str, expected_filter: str) -> None:
    expected_policy = _FILTER_TO_POLICY[expected_filter]
    assert future_date_policy(question, _TODAY) == expected_policy


@pytest.mark.parametrize(
    "question,policy",
    [
        ("revenue next month", "allow_future"),
        ("forecast revenue for Q3", "allow_future"),
        ("ledger revenue in August", "allow_future"),
        ("revenue in 2027", "allow_future"),
    ],
)
def test_explicit_future_cues_allow_future(question: str, policy: str) -> None:
    assert future_date_policy(question, _TODAY) == policy


@pytest.mark.parametrize(
    "question",
    [
        "how many open jobs",
        "top customers by invoiced sales",
    ],
)
def test_non_date_questions_default_exclude_future(question: str) -> None:
    assert future_date_policy(question, _TODAY) == "exclude_future"


def test_full_year_phrasing_mid_year_returns_clarify() -> None:
    assert future_date_policy("revenue for the full year 2026", _TODAY) == "clarify"


def test_future_date_rules_block_nonempty_for_all_policies() -> None:
    assert "post_date is after Today" in future_date_rules_block("exclude_future")
    assert "future calendar period" in future_date_rules_block("allow_future").lower()
    assert "CLARIFY:" in future_date_rules_block("clarify")
    assert "exclude_future" not in future_date_rules_block("allow_future")


def test_dated_prefix_injects_future_date_guard_for_exclude_policy(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "future_date.db"
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

    sql_chain.get_sql_chain(
        ["all"],
        "admin",
        db=db,
        anonymizer=anon,
        chat_deployment="gpt-4o-mini",
        today=_TODAY,
        question="revenue by month this year",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    content = captured["system_content"]
    assert "FUTURE POST_DATE GUARD (default" in content
    assert "Exclude rows whose post_date is after Today" in content
    assert "MONEY METRICS" in content


def test_dated_prefix_injects_allow_future_rule_when_future_month_named(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "future_date2.db"
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

    sql_chain.get_sql_chain(
        ["all"],
        "admin",
        db=db,
        anonymizer=anon,
        chat_deployment="gpt-4o-mini",
        today=_TODAY,
        question="revenue in July 2026",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    content = captured["system_content"]
    assert "FUTURE POST_DATE GUARD (explicit future period requested)" in content
    assert "do not clip to Today" in content
