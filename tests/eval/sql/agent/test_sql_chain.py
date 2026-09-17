"""
Tests for the SQL agent chain.

These tests verify:
1. The SQL agent can answer basic structured questions
2. Table-level access control works (users only see their tier's tables)
3. The agent handles edge cases gracefully

Run: pytest tests/rag/chains/test_sql_chain.py -v

NOTE: These are integration tests that require:
- A running MySQL database with the billing schema
- The BILLING_DATABASE_URL env var set
- The OpenAI API key set
"""

import os

import pytest
from langchain_core.language_models.fake import FakeListLLM
from sqlalchemy import create_engine, text

from app.eval.sql.agent import agent as sql_chain
from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.agent import get_allowed_tables, get_sql_chain
from app.rag.tier_scope import TierScope
from app.telemetry.runnable import _SpanWrappedRunnable, instrument_llm

# ---------------------------------------------------------------------------
# Fixtures — lightweight sqlite + fake LLM (no MySQL, no API)
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_products_engine(tmp_path):
    """Minimal sqlite schema so SQLDatabase can introspect allowed tables."""
    db_file = tmp_path / "test_products.db"
    engine = create_engine(f"sqlite:///{db_file}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE products "
                "(id INTEGER PRIMARY KEY, name TEXT, unit_price REAL, is_active INTEGER);"
            )
        )
        conn.execute(
            text("CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER);")
        )
        conn.execute(
            text("CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT, is_active INTEGER);")
        )
        conn.execute(
            text(
                "INSERT INTO products VALUES "
                "(1, 'Business Cards', 25.0, 1), (2, 'Flyers', 15.0, 1);"
            )
        )
    return engine


@pytest.fixture
def fake_sql_llm():
    return FakeListLLM(responses=["ok"])


# ---------------------------------------------------------------------------
# Unit tests — no database or LLM needed
# ---------------------------------------------------------------------------


class TestGetAllowedTables:
    """Test the tier-to-table mapping logic."""

    def test_all_tier_gets_base_tables(self):
        """Every user gets at least the 'all' tier tables."""
        tables = get_allowed_tables(TierScope.wildcard(["all"]))
        assert "products" in tables
        assert "categories" in tables
        assert "departments" in tables

    def test_all_tier_excludes_restricted_tables(self):
        """The 'all' tier should NOT include finance or sales tables."""
        tables = get_allowed_tables(TierScope.wildcard(["all"]))
        assert "customers" not in tables
        assert "bills" not in tables
        assert "journals" not in tables

    def test_sales_tier_adds_customer_tables(self):
        """Sales tier adds customer-related tables."""
        tables = get_allowed_tables(TierScope.wildcard(["all", "sales"]))
        assert "customers" in tables
        assert "customer_orders" in tables
        # Should still have base tables
        assert "products" in tables

    def test_finance_tier_adds_accounting_tables(self):
        """Finance tier adds accounting-related tables."""
        tables = get_allowed_tables(TierScope.wildcard(["all", "finance"]))
        assert "bills" in tables
        assert "journals" in tables
        assert "accounts" in tables
        # Should NOT have sales tables
        assert "customers" not in tables

    def test_warehouse_tier_adds_inventory_tables(self):
        """Warehouse tier adds inventory tables."""
        tables = get_allowed_tables(TierScope.wildcard(["all", "warehouse"]))
        assert "inventories" in tables
        assert "inventory_items" in tables

    def test_admin_gets_all_tables(self):
        """Admin tier gets every table from every tier."""
        tables = get_allowed_tables(TierScope.wildcard(["admin"]))
        assert "products" in tables
        assert "customers" in tables
        assert "bills" in tables
        assert "inventories" in tables

    def test_no_duplicate_tables(self):
        """Multiple tiers with overlapping tables shouldn't produce duplicates."""
        tables = get_allowed_tables(TierScope.wildcard(["all", "sales", "finance", "warehouse"]))
        assert len(tables) == len(set(tables))

    def test_unknown_tier_ignored(self):
        """An unrecognized tier name should not cause errors."""
        tables = get_allowed_tables(TierScope.wildcard(["all", "nonexistent_tier"]))
        # Should still have base tables, no crash
        assert "products" in tables

    def test_empty_tiers_returns_empty(self):
        """No tiers = no tables."""
        tables = get_allowed_tables(TierScope.wildcard([]))
        assert tables == []


class TestGetSqlChainConstruction:
    """Fast construction tests — catch wiring/type errors before integration."""

    @pytest.fixture(autouse=True)
    def _limit_tables_to_sqlite_schema(self, monkeypatch):
        """Fixture DB only has `products`; avoid include_tables validation errors."""
        monkeypatch.setattr(
            sql_chain, "get_allowed_tables", lambda tiers, strict_tier_scope=False: ["products"]
        )

    def test_constructs_with_base_language_model(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        """get_sql_chain must accept a real BaseLanguageModel (not a Runnable wrapper)."""
        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: fake_sql_llm)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        agent = get_sql_chain(
            ["all"],
            role="admin",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )

        assert agent is not None
        assert isinstance(agent, _SpanWrappedRunnable)

    def test_constructs_traced_wrapper_when_anonymization_enabled(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: fake_sql_llm)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", True)

        agent = get_sql_chain(
            ["all"],
            role="sales",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )

        assert agent is not None
        assert isinstance(agent, _SpanWrappedRunnable)

    def test_privileged_role_still_gets_anonymizer(self, monkeypatch, sqlite_products_engine):
        from app.config import settings
        from app.eval.sql.agent.agent import build_sql_database

        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr(settings, "redaction_enabled", True)
        _db, anon = build_sql_database(["all"], role="admin")
        assert anon is not None
        assert anon._privileged_pseudonymize is True

    def test_v2_privileged_role_never_gets_real_values(self, monkeypatch, sqlite_products_engine):
        """build_sql_database(..., strict_tier_scope=True) must construct an
        anonymizer with _privileged_pseudonymize False regardless of role."""
        from app.config import settings
        from app.eval.sql.agent.agent import build_sql_database

        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr(settings, "redaction_enabled", True)
        _db, anon = build_sql_database(["all"], role="admin", strict_tier_scope=True)
        assert anon is not None
        assert anon._privileged_pseudonymize is False

    def test_v1_build_sql_database_default_unaffected(self, monkeypatch, sqlite_products_engine):
        """strict_tier_scope defaults to False — every existing v1 call site
        (no kwarg) stays byte-identical."""
        from app.config import settings
        from app.eval.sql.agent.agent import build_sql_database

        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr(settings, "redaction_enabled", True)
        _db, anon = build_sql_database(["all"], role="admin")
        assert anon._privileged_pseudonymize is True

    def test_v2_get_sql_chain_threads_strict_tier_scope_to_anonymizer(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        """get_sql_chain(..., strict_tier_scope=True) must build the
        anonymizer with the same strict_tier_scope flag when it constructs
        its own db/anonymizer pair."""
        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: fake_sql_llm)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        captured: dict = {}
        real_build = sql_chain.build_sql_database

        def spy_build(*args, **kwargs):
            db, anon = real_build(*args, **kwargs)
            captured["strict_tier_scope"] = kwargs.get("strict_tier_scope", False)
            captured["privileged"] = anon._privileged_pseudonymize
            return db, anon

        monkeypatch.setattr(sql_chain, "build_sql_database", spy_build)

        get_sql_chain(
            ["all"],
            "admin",
            strict_tier_scope=True,
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )

        assert captured["strict_tier_scope"] is True
        assert captured["privileged"] is False

    def test_openrouter_deployment_without_controls_raises(
        self, monkeypatch, sqlite_products_engine
    ):
        """Slice 5 fail-closed: no silent 'low' default for the SQL lane."""
        from app.providers.model_registry import PolicyViolationError

        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)
        monkeypatch.setattr("app.config.settings.openrouter_api_key", "or-key")

        with pytest.raises(PolicyViolationError):
            get_sql_chain(
                ["all"],
                role="admin",
                chat_deployment="deepseek/deepseek-v4-flash-0731",
                row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
                allow_eval_fixture=True,
            )

    def test_openrouter_deployment_with_controls_reaches_get_chat_model(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        from app.providers.openrouter_controls import default_eval_controls

        captured: dict = {}

        def fake_get_chat_model(**kwargs):
            captured.update(kwargs)
            return fake_sql_llm

        monkeypatch.setattr(sql_chain, "get_chat_model", fake_get_chat_model)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)
        monkeypatch.setattr("app.config.settings.openrouter_api_key", "or-key")

        controls = default_eval_controls(reasoning_effort="high")
        get_sql_chain(
            ["all"],
            role="admin",
            chat_deployment="deepseek/deepseek-v4-flash-0731",
            controls=controls,
            request_timeout_s=300.0,
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )

        assert captured["controls"] == controls
        assert captured["request_timeout_s"] == 300.0

    def test_azure_deployment_without_controls_is_unaffected(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        """The fail-closed check only fires for OpenRouter; Azure is untouched."""
        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: fake_sql_llm)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        agent = get_sql_chain(
            ["all"],
            role="admin",
            chat_deployment="gpt-4o-mini",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        assert agent is not None

    def test_default_chat_deployment_none_is_unaffected(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        """chat_deployment=None (production default) never resolves to OpenRouter for
        ModelPurpose.sql_agent, so the fail-closed check must not even attempt it."""
        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: fake_sql_llm)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        agent = get_sql_chain(
            ["all"],
            role="admin",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        assert agent is not None

    def test_reasoning_effort_and_verbosity_reach_get_chat_model_for_azure_sql(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        captured: dict = {}

        def fake_get_chat_model(**kwargs):
            captured.update(kwargs)
            return fake_sql_llm

        monkeypatch.setattr(sql_chain, "get_chat_model", fake_get_chat_model)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        get_sql_chain(
            ["all"],
            role="admin",
            chat_deployment="gpt-5.6-luna",
            reasoning_effort="high",
            verbosity="low",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )

        assert captured["reasoning_effort"] == "high"
        assert captured["verbosity"] == "low"

    def test_prefix_does_not_name_restricted_columns(self):
        from app.eval.sql.agent.agent import SQL_AGENT_PREFIX

        assert "policy-restricted" in SQL_AGENT_PREFIX.lower()
        assert "account_number" not in SQL_AGENT_PREFIX
        assert "password" not in SQL_AGENT_PREFIX

    def test_register_enforcement_survives_redaction_flag_disabled(self, monkeypatch, tmp_path):
        from app.eval.sql.agent.agent import build_sql_database

        db_path = tmp_path / "accounts.db"
        sqlite_engine = create_engine(f"sqlite:///{db_path}")
        with sqlite_engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE accounts (id INTEGER PRIMARY KEY, account_number TEXT, bank TEXT)"
                )
            )
            conn.execute(text("INSERT INTO accounts VALUES (1, '0404617000', 'PBZ')"))

        monkeypatch.setattr(sql_chain, "engine", sqlite_engine)
        monkeypatch.setattr(
            sql_chain, "get_allowed_tables", lambda tiers, strict_tier_scope=False: ["accounts"]
        )
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        db, anon = build_sql_database(["finance"], role="sales")

        assert anon is not None
        assert "0404617000" not in db.run("SELECT * FROM accounts")
        assert "[SUPPRESSED]" in db.run("SELECT * FROM accounts")
        assert db.run_no_throw("SELECT account_number FROM accounts").startswith("Error")

    def test_get_sql_chain_requires_explicit_role(self):
        with pytest.raises(TypeError):
            get_sql_chain(
                ["all"], row_policy=ScopedSqlPolicy.eval_snapshot_fixture(), allow_eval_fixture=True
            )  # type: ignore[call-arg]

    def test_get_sql_chain_requires_row_policy(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        """Compile gate: omit/None row_policy → TypeError (Phase 0 denies before build)."""
        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: fake_sql_llm)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        with pytest.raises(TypeError, match="row_policy"):
            get_sql_chain(["all"], role="admin")  # type: ignore[call-arg]
        with pytest.raises(TypeError, match="row_policy"):
            get_sql_chain(["all"], role="admin", row_policy=None)

    def test_build_sql_database_disables_schema_sample_rows(
        self, monkeypatch, sqlite_products_engine
    ):
        """Phase 0 containment (Ask AI context/access plan): schema introspection
        must never sample live rows ahead of row-level authorization."""
        from app.eval.sql.agent.agent import build_sql_database

        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        db, _anon = build_sql_database(["all"], role="sales")

        assert db._sample_rows_in_table_info == 0
        # LangChain's SQLDatabase.get_table_info() gates sample-row rendering on
        # `if self._sample_rows_in_table_info:` — 0 is falsy, so no row-sampling
        # SQL is issued and no "N rows from ..." block appears at all.
        assert "rows from products table" not in db.get_table_info()
        assert "paper" not in db.get_table_info()

    def test_prompt_substitution_fills_placeholders(self):
        from app.config import settings
        from app.eval.sql.agent.agent import SQL_AGENT_PREFIX

        top_k = settings.sql_tool_max_rows
        filled = SQL_AGENT_PREFIX.format(dialect="mysql", top_k=top_k)
        assert "mysql" in filled and str(top_k) in filled
        assert "{dialect}" not in filled and "{top_k}" not in filled

    def test_get_sql_chain_rejects_db_anonymizer_mismatch(
        self, monkeypatch, sqlite_products_engine, fake_sql_llm
    ):
        from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer

        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: fake_sql_llm)
        db = AnonymizingSQLDatabase(sqlite_products_engine, anonymizer=None)
        anon = SqlAnonymizer("q", role="sales")

        with pytest.raises(ValueError, match="same anonymizer"):
            get_sql_chain(
                ["all"],
                role="sales",
                db=db,
                anonymizer=anon,
                row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
                allow_eval_fixture=True,
            )

    def test_instrument_llm_wrapper_accepted_at_construction(
        self, monkeypatch, sqlite_products_engine
    ):
        """build_sql_graph accepts any callable LLM at construction; bind_tools runs at invoke
        time so a _SpanWrappedRunnable no longer raises ValidationError on construction."""
        wrapped = instrument_llm(FakeListLLM(responses=["ok"]), operation="sql_agent")
        monkeypatch.setattr(sql_chain, "get_chat_model", lambda **kwargs: wrapped)
        monkeypatch.setattr(sql_chain, "engine", sqlite_products_engine)
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        agent = get_sql_chain(
            ["all"],
            role="admin",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        assert isinstance(agent, _SpanWrappedRunnable)

    def test_get_sql_chain_pins_injected_date(self, monkeypatch, sqlite_products_engine):
        from datetime import date
        from unittest.mock import MagicMock

        from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer

        captured: dict[str, str] = {}

        def capture_graph(db, llm, system_content, **kwargs):
            captured["sc"] = system_content
            return MagicMock()

        monkeypatch.setattr(sql_chain, "build_sql_graph", capture_graph)
        monkeypatch.setattr(
            sql_chain, "get_chat_model", lambda **kwargs: FakeListLLM(responses=["ok"])
        )
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        anon = SqlAnonymizer("q", role="manager")
        db = AnonymizingSQLDatabase(sqlite_products_engine, anonymizer=anon)

        get_sql_chain(
            ["finance"],
            "manager",
            db=db,
            anonymizer=anon,
            today=date(2026, 6, 16),
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        assert "Today's date is 2026-06-16" in captured["sc"]

    def test_escalated_financial_rules_gated_to_escalation_path(
        self, monkeypatch, sqlite_products_engine
    ):
        """ESCALATED_FINANCIAL_RULES must reach the gpt-4.1 escalation prompt
        and stay OUT of the default mini prompt — the same gating guarantee
        as ESCALATED_PAYMENT_RULES, so shared prose never dilutes the mini
        path."""
        from unittest.mock import MagicMock

        from app.config import settings
        from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer

        captured: dict[str, str] = {}

        def capture_graph(db, llm, system_content, **kwargs):
            captured["sc"] = system_content
            return MagicMock()

        monkeypatch.setattr(sql_chain, "build_sql_graph", capture_graph)
        monkeypatch.setattr(
            sql_chain, "get_chat_model", lambda **kwargs: FakeListLLM(responses=["ok"])
        )
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        anon = SqlAnonymizer("q", role="manager")
        db = AnonymizingSQLDatabase(sqlite_products_engine, anonymizer=anon)
        marker = "STANDING BALANCE"  # unique to ESCALATED_FINANCIAL_RULES

        # Escalation path → block present (rides with ESCALATED_PAYMENT_RULES).
        get_sql_chain(
            ["finance"],
            "manager",
            db=db,
            anonymizer=anon,
            chat_deployment=settings.azure_chat_escalation_deployment,
            route_reason="hard_financial",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        assert marker in captured["sc"]
        assert "PAYMENT-STATUS DEFINITIONS" in captured["sc"]

        # Default mini path → block absent.
        get_sql_chain(
            ["finance"],
            "manager",
            db=db,
            anonymizer=anon,
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        assert marker not in captured["sc"]

    def test_rules_block_before_prefix_episodic_after(self, monkeypatch, sqlite_products_engine):
        from unittest.mock import MagicMock

        from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer
        from app.eval.sql.agent.agent import SQL_AGENT_PREFIX

        captured: dict[str, str] = {}

        def capture_graph(db, llm, system_content, **kwargs):
            captured["sc"] = system_content
            return MagicMock()

        monkeypatch.setattr(sql_chain, "build_sql_graph", capture_graph)
        monkeypatch.setattr(
            sql_chain, "get_chat_model", lambda **kwargs: FakeListLLM(responses=["ok"])
        )
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        anon = SqlAnonymizer("q", role="manager")
        db = AnonymizingSQLDatabase(sqlite_products_engine, anonymizer=anon)
        prefix_marker = "You help staff answer questions"
        rules = "\nLEARNED RULES\n- rule one\n"
        episodic = "\nRETRIEVED EXAMPLES\nQ: x\nSQL: y\n"

        get_sql_chain(
            ["finance"],
            "manager",
            db=db,
            anonymizer=anon,
            rules_block=rules,
            episodic_block=episodic,
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        sc = captured["sc"]
        assert sc.index("Today's date is") < sc.index("LEARNED RULES")
        assert sc.index("LEARNED RULES") < sc.index(prefix_marker)
        assert sc.index(prefix_marker) < sc.index("RETRIEVED EXAMPLES")
        assert prefix_marker in SQL_AGENT_PREFIX

    def test_sql_prompt_defines_high_priority_like_the_jobs_ui(self):
        from app.eval.sql.agent.agent import SQL_AGENT_PREFIX

        assert "work_orders.priority = 'high'" in SQL_AGENT_PREFIX
        assert "work_orders.department_id <> 12" in SQL_AGENT_PREFIX

    def test_memory_blocks_with_literal_braces_do_not_crash_format(
        self, monkeypatch, sqlite_products_engine
    ):
        """A curated rule or exemplar containing a literal { or } must NOT reach
        str.format — only the prefix is formatted, dynamic blocks are concatenated after.
        Pre-fix this raised KeyError and crashed agent build whenever rules were enabled."""
        from unittest.mock import MagicMock

        from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer

        captured: dict[str, str] = {}

        def capture_graph(db, llm, system_content, **kwargs):
            captured["sc"] = system_content
            return MagicMock()

        monkeypatch.setattr(sql_chain, "build_sql_graph", capture_graph)
        monkeypatch.setattr(
            sql_chain, "get_chat_model", lambda **kwargs: FakeListLLM(responses=["ok"])
        )
        monkeypatch.setattr("app.config.settings.redaction_enabled", False)

        anon = SqlAnonymizer("q", role="manager")
        db = AnonymizingSQLDatabase(sqlite_products_engine, anonymizer=anon)
        braces_rule = "\nLEARNED RULES\n- one row per period like {month: total}\n"
        braces_episodic = "\nRETRIEVED EXAMPLES\n- WHERE data = '{a}'\n"

        # Must not raise KeyError/ValueError from the braces.
        get_sql_chain(
            ["finance"],
            "manager",
            db=db,
            anonymizer=anon,
            rules_block=braces_rule,
            episodic_block=braces_episodic,
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        sc = captured["sc"]
        # Braces survive verbatim; the real prefix placeholders are still filled.
        assert "{month: total}" in sc and "'{a}'" in sc
        assert "{dialect}" not in sc and "{top_k}" not in sc


# ---------------------------------------------------------------------------
# Integration tests — require database + LLM
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_BILLING_SQL_AGENT_INTEGRATION") != "1",
    reason="requires explicit RUN_BILLING_SQL_AGENT_INTEGRATION=1 and a reachable billing schema",
)
class TestSqlAgent:
    """
    Integration tests for the SQL agent.
    These require a running database and OpenAI API access.
    Mark with @pytest.mark.integration so they can be skipped in CI.
    """

    def test_agent_returns_result_for_basic_query(self):
        """The agent should answer a simple count question."""
        agent = get_sql_chain(
            ["all"],
            role="sales",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        result = agent.invoke({"input": "How many products are there?"})
        # The output should contain a number
        assert "output" in result
        assert len(result["output"]) > 0

    def test_agent_raises_for_empty_tables(self):
        """Empty tier policy is a configuration bug, not a runtime state."""
        with pytest.raises(AssertionError, match="tier policy yielded zero tables"):
            get_sql_chain(
                [],
                role="sales",
                row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
                allow_eval_fixture=True,
            )

    def test_agent_cannot_see_restricted_tables(self):
        """
        A sales-tier agent should not be able to query finance tables.
        The journals table should not exist in the agent's schema.
        """
        agent = get_sql_chain(
            ["all", "sales"],
            role="sales",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
            allow_eval_fixture=True,
        )
        result = agent.invoke({"input": "Show me all journal entries"})
        output = result["output"].lower()
        # The agent should indicate it can't access journals
        # It might say "no table", "not available", "cannot", etc.
        assert (
            any(
                word in output
                for word in [
                    "not",
                    "cannot",
                    "no",
                    "don't",
                    "unable",
                    "available",
                ]
            )
            or "journal" not in output
        )
