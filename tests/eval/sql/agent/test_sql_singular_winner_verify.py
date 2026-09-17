"""Singular winner verification tests (Phase E3).

Oracle: evals/experiments/sql-post-canary-phase-e-contract.json hand fixtures —
never the implementation under test, never gold customer names, never live Neptune match.

Expected verify SQL shape (documented oracle, not asserted on live LLM):
  SELECT entity_id, COUNT(*) AS metric
  FROM jobs
  GROUP BY entity_id
  ORDER BY metric DESC
  LIMIT 2
If top-two metric values equal → CLARIFY; else answer top row only.
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
from app.eval.sql.agent.winner_verify import (
    needs_winner_verify,
    winner_verify_rules_block,
)

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "evals"
    / "experiments"
    / "sql-post-canary-phase-e-contract.json"
)
_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
_E3_FIXTURES: list[str] = [
    entry["question"]
    for entry in _CONTRACT["sub_slices"]["E3_singular_winner_verify"]["hand_fixtures"]
]

_TODAY = date(2026, 6, 16)


@pytest.mark.parametrize("question", _E3_FIXTURES)
def test_needs_winner_verify_matches_contract_oracle(question: str) -> None:
    assert needs_winner_verify(question) is True


@pytest.mark.parametrize(
    "question",
    [
        "how many open jobs",
        "list all customers",
        "top 5 customers by revenue",
        "revenue this month",
        "show every order last week",
    ],
)
def test_non_winner_questions_return_false(question: str) -> None:
    assert needs_winner_verify(question) is False


def test_winner_verify_rules_block_nonempty_when_active() -> None:
    block = winner_verify_rules_block(True)
    assert "SINGULAR WINNER VERIFICATION" in block
    assert "ORDER BY the metric DESC, LIMIT 2" in block
    assert "CLARIFY:" in block
    assert winner_verify_rules_block(False) == ""


def test_dated_prefix_injects_winner_verify_for_busiest_question(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "winner_verify.db"
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
        question="who is our busiest customer",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    content = captured["system_content"]
    assert "SINGULAR WINNER VERIFICATION (busiest / top entity" in content
    assert "LIMIT 2" in content
    assert "FUTURE POST_DATE GUARD" in content


def test_dated_prefix_omits_winner_verify_without_winner_intent(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "winner_verify2.db"
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
        question="how many open jobs",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    assert "SINGULAR WINNER VERIFICATION" not in captured["system_content"]


def test_synthetic_db_documents_verify_sql_shape(tmp_path) -> None:
    """Synthetic DB oracle: clear winner vs near-tie — expected verify query shape in comments."""
    db_path = tmp_path / "jobs_winner.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL)")
        )
        # Clear winner: customer 1 has 3 jobs, customer 2 has 1.
        for cid, n in [(1, 3), (2, 1)]:
            for _ in range(n):
                conn.execute(text("INSERT INTO jobs (customer_id) VALUES (:cid)"), {"cid": cid})

    verify_sql = """
        SELECT customer_id, COUNT(*) AS job_count
        FROM jobs
        GROUP BY customer_id
        ORDER BY job_count DESC
        LIMIT 2
    """
    with engine.connect() as conn:
        rows = conn.execute(text(verify_sql)).fetchall()
    assert len(rows) == 2
    assert rows[0][1] > rows[1][1], "clear winner — top two metrics must differ"

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO jobs (customer_id) VALUES (2)"))
        conn.execute(text("INSERT INTO jobs (customer_id) VALUES (2)"))

    with engine.connect() as conn:
        tied_rows = conn.execute(text(verify_sql)).fetchall()
    assert tied_rows[0][1] == tied_rows[1][1], "near-tie — CLARIFY expected per E3 rule"

    assert needs_winner_verify("which customer has the most jobs") is True
