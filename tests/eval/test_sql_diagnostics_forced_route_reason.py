"""Forced eval deployments must still set route_reason from is_hard_financial (V2 fairness)."""

from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake import FakeListLLM
from sqlalchemy import create_engine, text

from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer
from app.eval.sql.agent import agent as sql_chain

MARKER = "PAYMENT-STATUS DEFINITIONS"
FORCED = "gpt-5.6-luna"


@pytest.fixture
def _stub_sql_chain_capture_route(monkeypatch):
    """Capture get_sql_chain kwargs; do not build a real agent."""
    captured: list[dict] = []
    fake_agent = MagicMock()
    fake_agent.invoke_with_clarifications = MagicMock(
        return_value={
            "output": "ok",
            "clarifications": [],
            "clarify_replies": [],
            "n_clarifications": 0,
        }
    )

    def fake_get_sql_chain(*args, **kwargs):
        captured.append(kwargs)
        return fake_agent

    monkeypatch.setattr("app.eval.sql.agent.agent.get_sql_chain", fake_get_sql_chain)
    monkeypatch.setattr(
        "app.eval.sql.agent.agent.build_sql_database",
        lambda tiers, role: (MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(
        "app.eval.sql.diagnostics._bare_sql_agent",
        lambda agent: agent,
    )
    return captured


def test_forced_deployment_hard_financial_sets_route_reason(
    _stub_sql_chain_capture_route, monkeypatch
):
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    case = {
        "id": "test-forced-hard",
        "role": "finance",
        "permissions": [],
        "question": "how many invoices have been fully paid?",
        "gold_sql": "SELECT 1",
    }
    run_case(case, chat_deployment=FORCED)
    kwargs = _stub_sql_chain_capture_route[-1]
    assert kwargs["chat_deployment"] == FORCED
    assert kwargs["route_reason"] == "hard_financial"


def test_forced_deployment_non_hard_leaves_route_reason_none(
    _stub_sql_chain_capture_route, monkeypatch
):
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    case = {
        "id": "test-forced-nonhard",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    run_case(case, chat_deployment=FORCED)
    kwargs = _stub_sql_chain_capture_route[-1]
    assert kwargs["chat_deployment"] == FORCED
    assert kwargs["route_reason"] is None


def _prompt_capture_setup(monkeypatch, tmp_path, name: str):
    db_path = tmp_path / f"{name}.db"
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
    monkeypatch.setattr(
        "app.eval.sql.agent.agent.build_sql_database",
        lambda tiers, role: (db, anon),
    )
    fake_agent = MagicMock()
    fake_agent.invoke_with_clarifications = MagicMock(
        return_value={
            "output": "ok",
            "clarifications": [],
            "clarify_replies": [],
            "n_clarifications": 0,
        }
    )
    real_get = sql_chain.get_sql_chain

    def wrapping_get(*args, **kwargs):
        real_get(*args, **kwargs)
        return fake_agent

    monkeypatch.setattr("app.eval.sql.agent.agent.get_sql_chain", wrapping_get)
    monkeypatch.setattr(
        "app.eval.sql.diagnostics._bare_sql_agent",
        lambda agent: agent,
    )
    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    return captured


def test_forced_hard_financial_prompt_includes_payment_defs(monkeypatch, tmp_path):
    """Fairness AC: forced + hard → PAYMENT-STATUS DEFINITIONS in prompt."""
    from app.eval.sql.diagnostics import run_case

    captured = _prompt_capture_setup(monkeypatch, tmp_path, "hard")
    case = {
        "id": "test-forced-hard-prompt",
        "role": "finance",
        "permissions": [],
        "question": "how many invoices have been fully paid?",
        "gold_sql": "SELECT 1",
    }
    run_case(case, chat_deployment=FORCED)
    assert MARKER in captured["system_content"]


def test_forced_non_hard_prompt_omits_payment_defs(monkeypatch, tmp_path):
    """Fairness AC: forced + non-hard → payment defs absent."""
    from app.eval.sql.diagnostics import run_case

    captured = _prompt_capture_setup(monkeypatch, tmp_path, "nonhard")
    case = {
        "id": "test-forced-nonhard-prompt",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    run_case(case, chat_deployment=FORCED)
    assert MARKER not in captured["system_content"]
