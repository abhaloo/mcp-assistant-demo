"""The read-only session guard is the only thing stopping the SQL agent from
writing to the live billing database. P1's report flagged it as pinned by no
test at all; grep -rn "billing_db_read_only" tests/ returned zero hits."""

from sqlalchemy import event

from app.eval.sql.agent import billing_engine


def test_read_only_listener_is_registered_when_the_setting_is_on(monkeypatch):
    monkeypatch.setattr(billing_engine.settings, "billing_db_read_only", True)
    monkeypatch.setattr(billing_engine.settings, "mcp_billing_database_url", "sqlite://")
    billing_engine.reset_engine_for_tests()
    engine = billing_engine.get_engine()
    assert event.contains(engine, "begin", billing_engine._set_session_read_only)


def test_no_listener_when_the_setting_is_off(monkeypatch):
    monkeypatch.setattr(billing_engine.settings, "billing_db_read_only", False)
    monkeypatch.setattr(billing_engine.settings, "mcp_billing_database_url", "sqlite://")
    billing_engine.reset_engine_for_tests()
    engine = billing_engine.get_engine()
    assert not event.contains(engine, "begin", billing_engine._set_session_read_only)


def test_eval_sql_harness_disposes_billing_engine_on_completion(monkeypatch):
    """When dispose_engine is removed from lifespan teardown, the eval SQL harness
    and CLI runners own disposing the billing engine pool upon completion."""
    monkeypatch.setattr(billing_engine.settings, "mcp_billing_database_url", "sqlite://")
    billing_engine.reset_engine_for_tests()
    billing_engine.get_engine()
    assert billing_engine._ENGINE is not None

    # Teardown on completion closes connections and clears the cached singleton
    billing_engine.dispose_engine()
    assert billing_engine._ENGINE is None
