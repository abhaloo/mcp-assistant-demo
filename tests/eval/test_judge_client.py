"""Judge client must use Azure AD token provider (same as evaluate_ragas)."""

from __future__ import annotations

from unittest.mock import MagicMock


def test_get_judge_client_azure_uses_token_provider(monkeypatch):
    import app.eval.document_rag.ragas_judge_client as judge_mod

    fake_settings = MagicMock()
    fake_settings.chat_provider = "azure"
    fake_settings.azure_endpoint = "https://example.openai.azure.com/"
    fake_settings.azure_api_version = "2024-12-01-preview"
    fake_settings.model_max_retries = 1
    fake_settings.model_request_timeout_s = 60.0
    monkeypatch.setattr(judge_mod, "settings", fake_settings)

    captured: dict = {}

    class FakeAzureOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(judge_mod, "AzureOpenAI", FakeAzureOpenAI)
    monkeypatch.setattr(
        judge_mod,
        "get_token_provider",
        lambda scope: "token-provider-callable",
    )
    monkeypatch.setattr(judge_mod, "COGNITIVE_SERVICES_SCOPE", "scope")

    client = judge_mod.get_judge_client()
    assert client is not None
    assert captured.get("azure_ad_token_provider") == "token-provider-callable"
    assert "api_key" not in captured


def test_get_judge_llm_accepts_deployment_override(monkeypatch):
    import app.eval.document_rag.ragas_judge_client as judge_mod

    fake_settings = MagicMock()
    fake_settings.chat_provider = "openai"
    fake_settings.active_chat_model = "gpt-4o-mini"
    fake_settings.model_api_key = "k"
    fake_settings.base_url = "https://example.com"
    fake_settings.model_request_timeout_s = 60.0
    monkeypatch.setattr(judge_mod, "settings", fake_settings)
    monkeypatch.setattr(judge_mod, "OpenAI", lambda **kwargs: "openai-client")

    captured: dict = {}

    def fake_factory(*, model, client):
        captured["model"] = model
        captured["client"] = client
        return "llm"

    llm = judge_mod.get_judge_llm(deployment="gpt-5.6-luna", llm_factory_fn=fake_factory)
    assert llm == "llm"
    assert captured["model"] == "gpt-5.6-luna"
    assert captured["client"] == "openai-client"


def test_patch_instructor_llm_args_strips_for_luna():
    import app.eval.document_rag.ragas_judge_client as judge_mod

    class Fake:
        model_args = {"temperature": 0.01, "top_p": 0.1, "max_tokens": 1024}

    llm = Fake()
    judge_mod.patch_instructor_llm_args(llm, model="gpt-5.6-luna")
    assert "max_tokens" not in llm.model_args
    assert "temperature" not in llm.model_args
    assert "top_p" not in llm.model_args
    assert llm.model_args["max_completion_tokens"] == 8192


def test_patch_instructor_llm_args_leaves_mini_alone():
    import app.eval.document_rag.ragas_judge_client as judge_mod

    class Fake:
        model_args = {"temperature": 0.01, "top_p": 0.1, "max_tokens": 1024}

    llm = Fake()
    judge_mod.patch_instructor_llm_args(llm, model="gpt-4o-mini")
    assert llm.model_args["max_tokens"] == 1024
    assert llm.model_args["temperature"] == 0.01
