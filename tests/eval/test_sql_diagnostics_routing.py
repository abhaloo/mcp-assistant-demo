"""run_case routes hard financial questions to the escalation deployment."""

from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.providers.model_purpose import ModelPurpose
from app.providers.route_settings import resolve_model_route


@pytest.fixture
def _stub_sql_chain(monkeypatch):
    """Avoid building a real agent; capture get_sql_chain kwargs instead."""
    captured: list[dict] = []
    fake_agent = MagicMock()
    fake_agent.invoke.return_value = {"output": "ok"}

    def fake_get_sql_chain(*args, **kwargs):
        captured.append(kwargs)
        return fake_agent

    monkeypatch.setattr("app.eval.sql.agent.agent.get_sql_chain", fake_get_sql_chain)
    monkeypatch.setattr(
        "app.eval.sql.agent.agent.build_sql_database",
        lambda tiers, role: (MagicMock(), MagicMock()),
    )
    return captured


def test_run_case_escalates_aging_question(_stub_sql_chain, monkeypatch):
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    case = {
        "id": "test-aging",
        "role": "finance",
        "permissions": [],
        "question": "break down receivables by aging bucket",
        "gold_sql": "SELECT 1",
    }
    result = run_case(case)
    assert _stub_sql_chain[-1]["chat_deployment"] == settings.azure_chat_escalation_deployment
    assert result["chat_deployment"] == settings.azure_chat_escalation_deployment


def test_run_case_default_for_simple_question(_stub_sql_chain, monkeypatch):
    """Also proves the CLI/eval/test seam exemption in passing: this test
    never sets SQL_POLICY_MODE=scoped, yet run_case() still constructs — the
    ScopedSqlPolicy.eval_snapshot_fixture() call inside run_case() is what
    makes that possible (see test_run_case_denies_before_constructing_agent
    for the direct proof)."""
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    case = {
        "id": "test-simple",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    result = run_case(case)
    expected = resolve_model_route(ModelPurpose.sql_agent, question=case["question"]).deployment
    assert _stub_sql_chain[-1]["chat_deployment"] == expected
    assert result["chat_deployment"] == expected


def test_run_case_forced_chat_deployment_skips_router(_stub_sql_chain, monkeypatch):
    """Explicit chat_deployment override for experiment arms — router not consulted."""
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.rag.model_router.select_chat_deployment",
        lambda q: (_ for _ in ()).throw(AssertionError("router must not run")),
    )
    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    forced = "deepseek/deepseek-chat"
    case = {
        "id": "test-forced",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    result = run_case(case, chat_deployment=forced)
    assert _stub_sql_chain[-1]["chat_deployment"] == forced
    assert result["chat_deployment"] == forced


def test_run_case_builds_openrouter_controls_from_reasoning_effort(_stub_sql_chain, monkeypatch):
    """Slice 5: SqlArm.reasoning_effort must reach get_sql_chain as controls for an
    OpenRouter deployment, not the ambient 'low' default."""
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    monkeypatch.setattr("app.config.settings.openrouter_api_key", "or-key")
    case = {
        "id": "test-openrouter",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    run_case(
        case,
        chat_deployment="deepseek/deepseek-v4-flash-0731",
        reasoning_effort="high",
        request_timeout_s=300.0,
    )
    kwargs = _stub_sql_chain[-1]
    assert kwargs["controls"].reasoning_effort == "high"
    assert kwargs["reasoning_effort"] is None
    assert kwargs["request_timeout_s"] == 300.0


def test_run_case_honors_profile_provider_order_for_openrouter(_stub_sql_chain, monkeypatch):
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    monkeypatch.setattr("app.config.settings.openrouter_api_key", "or-key")
    case = {
        "id": "test-openrouter-order",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    run_case(
        case,
        chat_deployment="deepseek/deepseek-v4-flash-0731",
        reasoning_effort="high",
        provider_order=["Novita"],
    )
    kwargs = _stub_sql_chain[-1]
    assert kwargs["controls"].order == ["Novita"]
    assert kwargs["controls"].only == ["Novita"]


def test_run_case_passes_reasoning_effort_directly_for_azure_deployment(
    _stub_sql_chain, monkeypatch
):
    """The same reasoning_effort input reaches the AZURE kwarg (not controls) for an
    Azure deployment, since it has no OpenRouterControls wire shape."""
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    case = {
        "id": "test-azure-effort",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    run_case(case, chat_deployment="gpt-5.6-luna", reasoning_effort="high")
    kwargs = _stub_sql_chain[-1]
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["controls"] is None


def test_run_case_without_reasoning_effort_passes_no_controls(_stub_sql_chain, monkeypatch):
    """Default behavior (no reasoning_effort given) stays byte-identical: no controls
    built, existing Azure-only callers untouched."""
    from app.eval.sql.diagnostics import run_case

    monkeypatch.setattr(
        "app.eval.sql.diagnostics.score_case",
        lambda db, agent_sql, gold_sql, ordered, **_: {"match": True, "valid_sql": True},
    )
    case = {
        "id": "test-default",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    run_case(case)
    kwargs = _stub_sql_chain[-1]
    assert kwargs["controls"] is None
    assert kwargs["reasoning_effort"] is None
    assert kwargs["request_timeout_s"] is None


def test_run_case_denies_before_constructing_agent(monkeypatch):
    """Ask AI context/access plan, Phase 0 review finding: eval/CLI callers
    must route through the seam with an explicit fixture, not bypass it by
    never calling it. Proves the ORDER too — a seam denial must stop
    construction before build_sql_database / get_sql_chain are ever reached."""
    from app.eval.sql import diagnostics
    from app.eval.sql.agent.access import StructuredAccessDenied

    monkeypatch.setattr(
        diagnostics,
        "ensure_scoped_sql_access",
        MagicMock(side_effect=StructuredAccessDenied("structured SQL is currently disabled")),
    )
    build_db = MagicMock()
    build_chain = MagicMock()
    monkeypatch.setattr("app.eval.sql.agent.agent.build_sql_database", build_db)
    monkeypatch.setattr("app.eval.sql.agent.agent.get_sql_chain", build_chain)

    case = {
        "id": "test-seam-denied",
        "role": "finance",
        "permissions": [],
        "question": "how many orders this week?",
        "gold_sql": "SELECT 1",
    }
    with pytest.raises(StructuredAccessDenied):
        diagnostics.run_case(case)

    build_db.assert_not_called()
    build_chain.assert_not_called()
