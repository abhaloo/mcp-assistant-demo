from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from app.eval.sql.diagnostics import CapturingCallback, score_case


class _FakeDB:
    """Minimal stand-in for AnonymizingSQLDatabase._execute over a sqlite engine."""

    def __init__(self, engine):
        self._engine = engine

    def _execute(self, command, fetch="all", **kwargs):
        with self._engine.connect() as conn:
            rows = conn.execute(text(command)).mappings().all()
        return [dict(r) for r in rows]


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE c (id INTEGER, name TEXT)"))
        conn.execute(text("INSERT INTO c VALUES (1,'a'),(2,'b'),(3,NULL)"))
        conn.commit()
    return _FakeDB(engine)


def test_callback_records_sql_and_attempts():
    cb = CapturingCallback()
    rid = uuid4()
    cb.on_tool_start({"name": "sql_db_query"}, "SELECT COUNT(*) FROM c", run_id=rid)
    cb.on_tool_end("3", run_id=rid)
    assert cb.final_sql == "SELECT COUNT(*) FROM c"
    assert len(cb.sql_queries) == 1
    assert "sql_db_query" in cb.tool_ms


def test_callback_ignores_non_query_tools():
    cb = CapturingCallback()
    rid = uuid4()
    cb.on_tool_start({"name": "sql_db_schema"}, "c", run_id=rid)
    cb.on_tool_end("schema...", run_id=rid)
    assert cb.final_sql is None


def test_score_case_match(db):
    out = score_case(db, "SELECT COUNT(*) FROM c", "SELECT COUNT(*) FROM c", ordered=None)
    assert out["match"] is True
    assert out["valid_sql"] is True


def test_score_case_invalid_sql_sets_flag(db):
    out = score_case(db, "SELECT * FROM nonexistent", "SELECT COUNT(*) FROM c", ordered=None)
    assert out["valid_sql"] is False
    assert out["match"] is False


def test_score_case_no_query(db):
    out = score_case(db, None, "SELECT COUNT(*) FROM c", ordered=None)
    assert out["match"] is False
    assert out["reason"] == "no_agent_query"


# --- PII / tokenized-scoring path (Finding 3) -------------------------------------
# Use a REAL AnonymizingSQLDatabase so the token round-trip is exercised. customers.name
# is a forced PII column -> deterministic tokenization (no Presidio NER needed).


def test_score_matches_token_literal_against_real_value(tmp_path, monkeypatch):
    """Agent SQL filtering on a token (WHERE name='<PERSON_1>') must match gold filtering
    on the real value — both run through the SAME anonymizer that minted the token."""
    monkeypatch.setattr("app.config.settings.redaction_enabled", True)
    from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer

    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE customers (id INTEGER, name TEXT)"))
        conn.execute(text("INSERT INTO customers VALUES (1,'Ali Hassan'),(2,'Bob')"))
        conn.commit()
    anon = SqlAnonymizer(query_id="t", role="sales")
    db = AnonymizingSQLDatabase(engine, include_tables=["customers"], anonymizer=anon)

    db._execute("SELECT name FROM customers WHERE id = 1")  # primes 'Ali Hassan' -> token
    token = anon._reverse["Ali Hassan"]
    out = score_case(
        db,
        f"SELECT id FROM customers WHERE name = '{token}'",
        "SELECT id FROM customers WHERE name = 'Ali Hassan'",
        ordered=None,
    )
    assert out["match"] is True


def test_score_pii_column_results_match(tmp_path, monkeypatch):
    """Gold and agent both SELECT a PII column — both tokenize identically via the shared
    anonymizer, so the tokenized result sets compare equal."""
    monkeypatch.setattr("app.config.settings.redaction_enabled", True)
    from app.eval.sql.agent import AnonymizingSQLDatabase, SqlAnonymizer

    engine = create_engine(f"sqlite:///{tmp_path / 'p2.db'}")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE customers (id INTEGER, name TEXT)"))
        conn.execute(text("INSERT INTO customers VALUES (1,'Ali Hassan')"))
        conn.commit()
    anon = SqlAnonymizer(query_id="t2", role="sales")
    db = AnonymizingSQLDatabase(engine, include_tables=["customers"], anonymizer=anon)
    out = score_case(db, "SELECT name FROM customers", "SELECT name FROM customers", ordered=None)
    assert out["match"] is True


def test_callback_prefers_structured_query_input():
    """When the tool is called with structured {"query": ...}, capture that — not the
    stringified dict that input_str may carry."""
    cb = CapturingCallback()
    rid = uuid4()
    cb.on_tool_start(
        {"name": "sql_db_query"},
        "{'query': 'SELECT 1'}",
        run_id=rid,
        inputs={"query": "SELECT 1"},
    )
    cb.on_tool_end("1", run_id=rid)
    assert cb.final_sql == "SELECT 1"


def test_scrub_masks_string_literals_independent_of_ner():
    """_scrub redacts ALL string literals regardless of Presidio config, keeping only
    the SQL structure — so names/emails never reach the run file."""
    from app.eval.sql.diagnostics import scrub

    out = scrub("SELECT id FROM customers WHERE name = 'Ali Hassan' AND email = 'a@b.com'")
    assert "Ali Hassan" not in out
    assert "a@b.com" not in out
    assert out == "SELECT id FROM customers WHERE name = ? AND email = ?"


def test_capturing_callback_records_all_tool_events():
    from uuid import uuid4

    from app.eval.sql.diagnostics import CapturingCallback

    cb = CapturingCallback()
    rid1, rid2 = uuid4(), uuid4()
    cb.on_tool_start(
        {"name": "sql_db_schema"}, "products", run_id=rid1, inputs={"tables": "products"}
    )
    cb.on_tool_start(
        {"name": "sql_db_query"},
        "",
        run_id=rid2,
        inputs={"query": "SELECT name FROM customers WHERE name = 'Ali'"},
    )
    names = [e["tool"] for e in cb.tool_events]
    assert names == ["sql_db_schema", "sql_db_query"]
    # the query event is literal-scrubbed (no raw value persisted)
    q_event = next(e for e in cb.tool_events if e["tool"] == "sql_db_query")
    assert "Ali" not in str(q_event)


class _Memory:
    rules_block = ""
    episodic_block = ""


class _StubDB:
    """Minimal db for run_case stubs — satisfies gold fetch in score_case."""

    def _execute(self, command, fetch="all", **kwargs):
        return [{"col": 1}]


def _stub_run_case_agent(monkeypatch, agent):
    """Minimal run_case stubs — same shape as test_sql_evidence_redaction site 1."""
    from app.eval.sql import diagnostics

    monkeypatch.setattr("app.rag.access_tiers.get_access_tiers", lambda *a, **k: ["all"])
    monkeypatch.setattr("app.rag.access_tiers.get_allowed_tables", lambda *a, **k: ["customers"])
    monkeypatch.setattr(
        "app.eval.sql.agent.agent.build_sql_database", lambda *a, **k: (_StubDB(), None)
    )
    monkeypatch.setattr("app.eval.sql.agent.agent.get_sql_chain", lambda *a, **k: agent)
    monkeypatch.setattr(diagnostics, "memory_for_eval_case", lambda *a, **k: _Memory())
    monkeypatch.setattr(diagnostics, "ensure_scoped_sql_access", lambda *a, **k: None)


def test_run_case_unwraps_span_wrapped_sql_agent(monkeypatch):
    """get_sql_chain returns wrap_with_span; clarify loop lives on the inner agent."""
    from app.eval.sql import diagnostics
    from app.telemetry.runnable import _SpanWrappedRunnable, wrap_with_span
    from app.telemetry.spans import guardrails_sql_anonymize_span

    class _Agent:
        def invoke_with_clarifications(self, *_args, **kwargs):
            cb = kwargs.get("config", {}).get("callbacks", [None])[0]
            if cb is not None:
                cb.n_llm_calls = 1
            return {
                "output": "ok",
                "clarifications": [],
                "clarify_replies": [],
                "n_clarifications": 0,
            }

    wrapped = wrap_with_span(_Agent(), guardrails_sql_anonymize_span)
    assert isinstance(wrapped, _SpanWrappedRunnable)
    _stub_run_case_agent(monkeypatch, wrapped)
    monkeypatch.setattr(
        diagnostics,
        "score_case",
        lambda *a, **k: {"match": True, "valid_sql": True, "reason": "ok"},
    )

    row = diagnostics.run_case(
        {"id": "c1", "question": "q", "role": "admin", "gold_sql": "SELECT 1", "permissions": []},
        frozenset(),
        chat_deployment="[REDACTED]",
    )

    assert row["n_llm_calls"] == 1
    assert row["agent_error"] is None


def test_run_case_budget_clarify_without_oracle_is_operational(monkeypatch):
    from app.eval.sql import diagnostics
    from app.eval.sql.agent.clarify import is_clarification_answer

    class _Agent:
        def invoke_with_clarifications(self, *_a, **_k):
            return {
                "output": "CLARIFY: which metric should I lock first?",
                "queries": [],
                "sql_stop_reason": "budget_clarify",
                "clarifications": ["which metric should I lock first?"],
                "clarify_replies": [],
                "n_clarifications": 1,
                "raw": {},
                "deanonymize": lambda x: x,
                "execution_records": [],
            }

        def invoke(self, *a, **k):
            return self.invoke_with_clarifications(*a, **k)

    _stub_run_case_agent(monkeypatch, _Agent())
    row = diagnostics.run_case(
        {"id": "c1", "question": "q", "role": "admin", "gold_sql": "SELECT 1", "permissions": []},
        frozenset(),
        chat_deployment="gpt-4o-mini",
    )
    assert row["operational_failure"] == "budget_clarify"
    assert row["match"] is None
    assert is_clarification_answer(row.get("agent_output") or row.get("output") or "CLARIFY: x")


def test_run_case_budget_clarify_with_oracle_scores_post_reply(monkeypatch):
    """Oracle reply → continuation SQL → no budget_clarify ops exclusion."""
    from app.eval.sql import diagnostics

    class _Agent:
        def invoke_with_clarifications(self, *_a, **_k):
            return {
                "output": "42",
                "queries": ["SELECT 42"],
                "sql_stop_reason": None,
                "clarifications": ["which metric?"],
                "clarify_replies": ["count only"],
                "n_clarifications": 1,
                "raw": {"answer": "42", "queries": ["SELECT 42"], "rows": ""},
                "deanonymize": lambda x: x,
                "execution_records": [],
            }

        def invoke(self, *a, **k):
            return self.invoke_with_clarifications(*a, **k)

    _stub_run_case_agent(monkeypatch, _Agent())
    monkeypatch.setattr(
        diagnostics,
        "score_case",
        lambda *a, **k: {
            "valid_sql": True,
            "error_category": None,
            "agent_row_count": 1,
            "gold_row_count": 1,
            "match": True,
            "reason": "exact",
        },
    )
    row = diagnostics.run_case(
        {
            "id": "c-oracle",
            "question": "how many?",
            "role": "admin",
            "gold_sql": "SELECT 42",
            "permissions": [],
            "clarify_answer": "count only",
        },
        frozenset(),
        chat_deployment="gpt-4o-mini",
    )
    assert row.get("operational_failure") is None
    assert row["match"] is True


def test_run_case_budget_clarify_clarify_ok_tag_is_diagnostic_not_ops(monkeypatch):
    """clarify-ok tag + no oracle → clarify_ok diagnostic; do not set budget_clarify ops."""
    from app.eval.sql import diagnostics
    from app.eval.sql.agent.clarify import CLARIFY_PREFIX

    class _Agent:
        def invoke_with_clarifications(self, *_a, **_k):
            return {
                "output": f"{CLARIFY_PREFIX} which filter?",
                "queries": [],
                "sql_stop_reason": "budget_clarify",
                "clarifications": ["which filter?"],
                "clarify_replies": [],
                "n_clarifications": 1,
                "raw": {},
                "deanonymize": lambda x: x,
                "execution_records": [],
            }

        def invoke(self, *a, **k):
            return self.invoke_with_clarifications(*a, **k)

    _stub_run_case_agent(monkeypatch, _Agent())
    row = diagnostics.run_case(
        {
            "id": "c-ok",
            "question": "q",
            "role": "admin",
            "gold_sql": "SELECT 1",
            "permissions": [],
            "tags": ["clarify-ok"],
        },
        frozenset(),
        chat_deployment="gpt-4o-mini",
    )
    assert row["error_category"] == "clarify_ok"
    assert row.get("operational_failure") is None


def test_run_case_budget_exceeded_is_operational_not_scored(monkeypatch):
    """Regression guard: wrapped budget stop must set operational_failure and exclude
    from scoring (match=None), not score as a model miss when SQL was attempted."""
    from app.eval.sql import diagnostics
    from app.eval.sql.agent import SqlAgentExecutionError
    from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded

    class _Agent:
        def invoke_with_clarifications(self, *_args, **_kwargs):
            try:
                raise SqlContextBudgetExceeded("next_prompt_tokens")
            except SqlContextBudgetExceeded as exc:
                raise SqlAgentExecutionError("budget_exceeded:next_prompt_tokens") from exc

        def invoke(self, *_args, **_kwargs):
            return self.invoke_with_clarifications(*_args, **_kwargs)

    _stub_run_case_agent(monkeypatch, _Agent())
    monkeypatch.setattr(
        diagnostics,
        "score_case",
        lambda *a, **k: {"match": False, "valid_sql": True, "reason": "would_be_miss"},
    )

    row = diagnostics.run_case(
        {"id": "c1", "question": "q", "role": "admin", "gold_sql": "SELECT 1", "permissions": []},
        frozenset(),
        chat_deployment="gpt-4o-mini",
    )

    assert row["operational_failure"] == "budget_exceeded"
    assert row["match"] is None
    assert row["agent_error"]
    assert "SqlAgentExecutionError" in row["agent_error"]
    assert "budget_exceeded:next_prompt_tokens" in row["agent_error"]


def _agent_emitting_reasoning(raw: str):
    """SQL agent stub that fires CapturingCallback with one reasoning body."""
    from types import SimpleNamespace
    from uuid import uuid4

    from langchain_core.messages import AIMessage

    def _llm_response(text: str) -> SimpleNamespace:
        msg = AIMessage(content="ok", additional_kwargs={"reasoning_content": text})
        return SimpleNamespace(generations=[[SimpleNamespace(message=msg)]], llm_output={})

    class _Agent:
        def invoke_with_clarifications(self, *_args, **kwargs):
            cb = kwargs.get("config", {}).get("callbacks", [None])[0]
            if cb is not None:
                run_id = uuid4()
                cb.on_llm_start({}, ["p"], run_id=run_id)
                cb.on_llm_end(_llm_response(raw), run_id=run_id)
            return {
                "output": "ok",
                "clarifications": [],
                "clarify_replies": [],
                "n_clarifications": 0,
            }

    return _Agent()


def test_run_case_reasoning_calls_survive_sanitize_under_defaults(monkeypatch):
    """Contract: run_case → sanitize keeps redacted+raw bodies when defaults on."""
    from app.eval.sql import diagnostics
    from app.experiments.answer_persistence import sanitize_answer_row_for_persistence

    secret = "sk-or-v1-NOTAREALKEYtestfixture0123456789abcdefNOTAREALKEY"
    raw = f"plan uses {secret}"

    _stub_run_case_agent(monkeypatch, _agent_emitting_reasoning(raw))
    monkeypatch.setattr(
        diagnostics,
        "score_case",
        lambda *a, **k: {"match": True, "valid_sql": True, "reason": "ok"},
    )

    row = diagnostics.run_case(
        {
            "id": "c1",
            "question": "q",
            "role": "admin",
            "gold_sql": "SELECT 1",
            "permissions": [],
        },
        frozenset(),
        chat_deployment="gpt-4o-mini",
    )
    assert len(row["reasoning_calls"]) == 1
    assert row["reasoning_calls"][0]["reasoning_text_raw"] == raw

    persisted = sanitize_answer_row_for_persistence(row)
    call = persisted["reasoning_calls"][0]
    assert call["reasoning_text_raw"] == raw
    assert secret in call["reasoning_text_raw"]
    assert secret not in call["reasoning_text"]
    assert "<redacted" in call["reasoning_text"]


# --- dual-estimator scoring --------------------------------------------------
# The scorer grades cb.sql_queries[-1] -- the last executed query, not
# necessarily the one that produced the final answer. A correct answer can
# be followed by an exploratory "for context" query that scores as a
# mismatch. Report both estimators so a baseline arm and a receipt-bearing
# candidate arm are never compared under different rulers.

_GOLD_NON_NULL = "SELECT COUNT(*) FROM c WHERE name IS NOT NULL"
_CORRECT = "SELECT COUNT(*) FROM c WHERE name IS NOT NULL"
_DISTRACTOR = "SELECT COUNT(*) FROM c"


def test_best_executed_query_finds_the_answer_a_later_probe_hid(db):
    from app.eval.sql.diagnostics import score_best_executed_query

    last = score_case(db, _DISTRACTOR, _GOLD_NON_NULL, ordered=None)
    assert last["match"] is False, "distractor must miss, or the fixture proves nothing"

    best = score_best_executed_query(db, [_CORRECT, _DISTRACTOR], _GOLD_NON_NULL, ordered=None)
    assert best["best_executed_query_match"] is True
    assert best["best_query_index"] == 0


def test_best_executed_query_agrees_when_the_last_query_is_right(db):
    from app.eval.sql.diagnostics import score_best_executed_query

    best = score_best_executed_query(db, [_DISTRACTOR, _CORRECT], _GOLD_NON_NULL, ordered=None)
    assert best["best_executed_query_match"] is True
    assert best["best_query_index"] == 1


def test_best_executed_query_is_false_when_nothing_matched(db):
    from app.eval.sql.diagnostics import score_best_executed_query

    best = score_best_executed_query(
        db, [_DISTRACTOR, "SELECT COUNT(*) FROM c WHERE id > 99"], _GOLD_NON_NULL, ordered=None
    )
    assert best["best_executed_query_match"] is False
    assert best["best_query_index"] is None


def test_best_executed_query_skips_invalid_sql_without_raising(db):
    from app.eval.sql.diagnostics import score_best_executed_query

    best = score_best_executed_query(
        db, ["SELECT * FROM nonexistent", _CORRECT], _GOLD_NON_NULL, ordered=None
    )
    assert best["best_executed_query_match"] is True
    assert best["best_query_index"] == 1


def test_best_executed_query_handles_no_queries(db):
    from app.eval.sql.diagnostics import score_best_executed_query

    best = score_best_executed_query(db, [], _GOLD_NON_NULL, ordered=None)
    assert best["best_executed_query_match"] is False
    assert best["best_query_index"] is None


def test_clarify_ok_with_no_sql_queries_persists_best_query_index_as_none(monkeypatch):
    from app.eval.sql import diagnostics

    class _ClarifyAgent:
        def __call__(self, *args, **kwargs):
            return {
                "output": "Could you clarify?",
                "clarifications": [{"question": "which one?"}],
                "clarify_replies": [],
                "n_clarifications": 1,
            }

    _stub_run_case_agent(monkeypatch, _ClarifyAgent())
    monkeypatch.setattr(
        diagnostics,
        "score_case",
        lambda *a, **k: {
            "match": True,
            "valid_sql": False,
            "reason": "clarify_ok",
            "error_category": "clarify_ok",
        },
    )

    row = diagnostics.run_case(
        {
            "id": "c_clarify",
            "question": "q",
            "role": "admin",
            "gold_sql": "SELECT 1",
            "permissions": [],
            "clarify_answer": "customer",
        },
        frozenset(),
        chat_deployment="gpt-4o-mini",
    )
    assert row["best_query_index"] is None
