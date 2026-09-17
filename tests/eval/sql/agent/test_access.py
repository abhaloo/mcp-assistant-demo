"""Unit tests for the structured-access seam (Ask AI context/access plan, Phase 0).

Covers the seam itself (app/rag/sql_access.py) and the SQL_POLICY_MODE setting
validation. Service-layer denial-matrix tests (JSON/SSE structured + BOTH) live
in tests/services/test_sql_containment.py and tests/api/test_ask_stream.py.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.eval.sql.agent.access import (
    ScopedSqlPolicy,
    StructuredAccessDenied,
    ensure_scoped_sql_access,
)


def _settings(**overrides) -> Settings:
    """Minimal valid Settings, same pattern as test_sql_graph.py."""
    return Settings(redaction_hmac_key="test-key", rag_jwt_secret="test-secret", **overrides)


class TestSqlPolicyModeSetting:
    def test_defaults_to_disabled(self):
        assert _settings().sql_policy_mode == "disabled"

    def test_accepts_scoped(self):
        assert _settings(sql_policy_mode="scoped").sql_policy_mode == "scoped"

    def test_unknown_value_fails_startup(self):
        with pytest.raises(ValidationError):
            _settings(sql_policy_mode="unscoped")


class TestEnsureScopedSqlAccess:
    def test_denies_by_default(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "sql_policy_mode", "disabled")
        with pytest.raises(StructuredAccessDenied):
            ensure_scoped_sql_access()

    def test_denial_message_does_not_leak_config_key_or_value(self, monkeypatch):
        """The message flows unfiltered into the client-visible JSON 503
        `detail` (app/api/router.py maps ServiceUnavailableError.detail
        straight through) — it must never name SQL_POLICY_MODE or its value."""
        from app.config import settings

        monkeypatch.setattr(settings, "sql_policy_mode", "disabled")
        with pytest.raises(StructuredAccessDenied) as exc_info:
            ensure_scoped_sql_access()
        message = str(exc_info.value)
        assert "sql_policy_mode" not in message.lower()
        assert "SQL_POLICY_MODE" not in message

    def test_scoped_mode_alone_without_policy_still_denies(self, monkeypatch):
        """The plan's guarantee, made literally true: 'a setting alone cannot
        create an unscoped database.' SQL_POLICY_MODE=scoped with no policy
        object must still deny — this is the review-finding fix, not the
        original Phase 0 behavior (which this test replaces)."""
        from app.config import settings

        monkeypatch.setattr(settings, "sql_policy_mode", "scoped")
        with pytest.raises(StructuredAccessDenied):
            ensure_scoped_sql_access()  # no policy supplied

    def test_policy_alone_without_scoped_mode_still_denies(self, monkeypatch):
        """A real (non-eval) policy object is not sufficient by itself either
        — both the setting AND the object are required together."""
        from app.config import settings

        monkeypatch.setattr(settings, "sql_policy_mode", "disabled")
        with pytest.raises(StructuredAccessDenied):
            ensure_scoped_sql_access(ScopedSqlPolicy(entity_id=1, cross_entity=False))

    def test_scoped_mode_with_complete_policy_passes(self, monkeypatch):
        """Both conditions together satisfy the seam — this is the path
        Phase 6's real per-request policy object will use."""
        from app.config import settings

        monkeypatch.setattr(settings, "sql_policy_mode", "scoped")
        ensure_scoped_sql_access(ScopedSqlPolicy(entity_id=1, cross_entity=False))


class TestScopedSqlPolicyScopeFields:
    def test_production_policy_requires_entity_or_cross_entity(self):
        with pytest.raises(ValueError):
            ScopedSqlPolicy(entity_id=None, cross_entity=False)

    def test_eval_fixture_marker_not_forgeable_via_public_kwarg(self):
        with pytest.raises((TypeError, ValueError)):
            ScopedSqlPolicy(is_eval_cli_fixture=True)  # type: ignore[call-arg]

    def test_transform_sql_delegates_to_arg_transform(self):
        from app.rag.sql_row_policy import ScopedSqlQuery

        policy = ScopedSqlPolicy(entity_id=42, cross_entity=False)
        result = policy.transform_sql("SELECT id FROM bills")
        assert isinstance(result, ScopedSqlQuery)
        assert 42 in result.params.values()
        assert "42" not in result.sql


class TestScopedSqlPolicyEvalCliFixture:
    """The CLI/eval/test row's exemption — unconditional, independent of
    SQL_POLICY_MODE, so the eval harness keeps working without flipping any
    production setting (review finding: eval/CLI paths must use an explicit
    fixture, not bypass the seam by never calling it)."""

    def test_eval_snapshot_fixture_is_marked(self):
        assert ScopedSqlPolicy.eval_snapshot_fixture().is_eval_cli_fixture is True
        assert ScopedSqlPolicy(entity_id=1).is_eval_cli_fixture is False

    def test_eval_snapshot_fixture_passes_while_disabled(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "sql_policy_mode", "disabled")
        ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())  # must not raise

    def test_eval_snapshot_fixture_passes_while_scoped(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "sql_policy_mode", "scoped")
        ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())  # must not raise
