"""SQL snapshot engine wiring tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import settings


def test_point_agent_at_snapshot_uses_connect_timeout_and_pool_recycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    fake_engine = MagicMock()

    def fake_create_engine(_url: str, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        return fake_engine

    monkeypatch.setattr(settings, "eval_snapshot_database_url", "mysql+pymysql://u:p@h/db")
    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    monkeypatch.setattr(
        "sqlalchemy.event.listens_for",
        lambda *_args, **_kwargs: lambda fn: fn,
    )

    import app.eval.sql.agent.agent as sql_chain_module
    from app.eval.sql import snapshot

    monkeypatch.setattr(sql_chain_module, "engine", MagicMock(), raising=False)

    snapshot._OWNED_SNAPSHOT_ENGINE = None
    snapshot.point_agent_at_snapshot(concurrency=2)

    assert captured["connect_args"] == {"connect_timeout": 10}
    assert captured["pool_recycle"] == 280
    assert sql_chain_module.engine is fake_engine
