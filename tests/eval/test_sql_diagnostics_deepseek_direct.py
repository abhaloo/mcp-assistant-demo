"""DeepSeek-direct SQL diagnostics seam tests (fake layer)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.eval.sql.agent.access import ScopedSqlPolicy
from app.providers.deepseek_direct_controls import DeepSeekDirectControls
from app.providers.model_registry import PolicyViolationError, resolve_model_spec


@pytest.fixture
def direct_settings(monkeypatch):
    s = Settings(
        redaction_hmac_key="test-key",
        rag_jwt_secret="test-secret",
        base_url="https://example.com/v1",
        model_api_key="test-api-key",
        _env_file=None,
        chat_provider="azure",
        azure_endpoint="https://x.openai.azure.com",
        environment="development",
        deepseek_direct_model="deepseek-v4-flash",
        deepseek_direct_base_url="https://api.deepseek.com",
        deepseek_direct_api_key="ds-key",
    )
    import app.config as config_mod
    import app.eval.sql.agent.agent as chain_mod
    import app.eval.sql.diagnostics as diag_mod

    for mod in (config_mod, diag_mod, chain_mod):
        monkeypatch.setattr(mod, "settings", s)
    return s


def test_direct_spec_supports_tools_permanently(direct_settings):
    spec = resolve_model_spec("deepseek-v4-flash", direct_settings)
    assert spec.credential_source == "deepseek_direct"
    assert spec.supports_tools is True


def test_run_case_routes_direct_effort_to_deepseek_direct_controls(monkeypatch, direct_settings):
    captured: dict = {}

    def fake_get_sql_chain(*args, **kwargs):
        captured.update(kwargs)
        agent = MagicMock()
        agent.invoke.return_value = {"output": "42"}
        return agent

    monkeypatch.setattr("app.eval.sql.agent.agent.get_sql_chain", fake_get_sql_chain)
    monkeypatch.setattr(
        "app.eval.sql.agent.agent.build_sql_database",
        lambda *a, **k: (MagicMock(), MagicMock()),
    )
    monkeypatch.setattr(
        "app.eval.sql.diagnostics.ensure_scoped_sql_access",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.eval.sql.diagnostics.memory_for_eval_case",
        lambda *a, **k: MagicMock(rules_block="", episodic_block=""),
    )

    from app.eval.sql.diagnostics import run_case

    case = {
        "id": "c1",
        "role": "warehouse",
        "permissions": ["view inventory"],
        "question": "How many items?",
        "gold_sql": "SELECT 1",
    }
    run_case(
        case,
        frozenset(),
        chat_deployment="deepseek-v4-flash",
        reasoning_effort="high",
    )

    controls = captured.get("deepseek_direct_controls")
    assert isinstance(controls, DeepSeekDirectControls)
    assert controls.reasoning_effort == "high"
    assert controls.thinking_enabled is True
    assert captured.get("controls") is None
    assert captured.get("reasoning_effort") is None


def test_sql_chain_fail_closed_without_direct_controls(direct_settings):
    from unittest.mock import MagicMock

    from app.eval.sql.agent.agent import get_sql_chain

    db = MagicMock()
    anonymizer = MagicMock()
    db._anonymizer = anonymizer

    with pytest.raises(PolicyViolationError, match="deepseek_direct_controls"):
        get_sql_chain(
            ["warehouse"],
            "warehouse",
            db=db,
            anonymizer=anonymizer,
            chat_deployment="deepseek-v4-flash",
            deepseek_direct_controls=None,
            row_policy=ScopedSqlPolicy(entity_id=1, cross_entity=False),
        )
