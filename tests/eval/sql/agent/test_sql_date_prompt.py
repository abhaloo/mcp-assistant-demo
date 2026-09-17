"""Dated prefix prefers recency and CLARIFY when relative dates stay ambiguous."""

from unittest.mock import MagicMock

from langchain_core.language_models.fake import FakeListLLM
from sqlalchemy import create_engine, text

from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer
from app.eval.sql.agent import agent as sql_chain
from app.eval.sql.agent.access import ScopedSqlPolicy


def test_dated_prefix_prefers_recency_and_clarify(monkeypatch, tmp_path):
    db_path = tmp_path / "date.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.commit()

    captured: dict[str, str] = {}

    def capture_graph(db, llm, system_content, **kwargs):
        captured["system_content"] = system_content
        captured["today"] = kwargs.get("today")
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
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    content = captured["system_content"]
    assert "most recent" in content.lower() or "prefer the most recent" in content.lower()
    assert "CLARIFY:" in content
    assert "sql_calendar_windows" in content
