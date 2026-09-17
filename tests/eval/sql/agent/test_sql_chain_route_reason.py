from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake import FakeListLLM
from sqlalchemy import create_engine, text

from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer
from app.eval.sql.agent import agent as sql_chain
from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.agent import ESCALATED_PAYMENT_RULES, get_sql_chain


@pytest.fixture
def sqlite_products_engine(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(text("INSERT INTO products (id, name) VALUES (1, 'paper')"))
        conn.commit()
    yield engine
    engine.dispose()


def _capture_system_content(monkeypatch, sqlite_products_engine):
    captured: dict[str, str] = {}

    def capture_graph(db, llm, system_content, **kwargs):
        captured["system_content"] = system_content
        return MagicMock()

    monkeypatch.setattr(sql_chain, "build_sql_graph", capture_graph)
    monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: FakeListLLM(responses=["ok"]))
    monkeypatch.setattr("app.config.settings.redaction_enabled", False)

    anon = SqlAnonymizer("q", role="admin")
    db = AnonymizingSQLDatabase(sqlite_products_engine, anonymizer=anon)
    return captured, db, anon


def test_escalated_rules_attach_on_hard_financial_reason_not_deployment_name(
    monkeypatch, sqlite_products_engine
):
    captured, db, anon = _capture_system_content(monkeypatch, sqlite_products_engine)
    marker = "PAYMENT-STATUS DEFINITIONS"
    assert marker in ESCALATED_PAYMENT_RULES
    get_sql_chain(
        ["all"],
        "admin",
        db=db,
        anonymizer=anon,
        chat_deployment="gpt-4o-mini",
        route_reason="hard_financial",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    assert marker in captured["system_content"]


def test_luna_deployment_without_hard_financial_reason_does_not_attach_rules(
    monkeypatch, sqlite_products_engine
):
    captured, db, anon = _capture_system_content(monkeypatch, sqlite_products_engine)
    marker = "PAYMENT-STATUS DEFINITIONS"
    get_sql_chain(
        ["all"],
        "admin",
        db=db,
        anonymizer=anon,
        chat_deployment="gpt-5.6-luna",
        route_reason=None,
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    assert marker not in captured["system_content"]
