"""No-progress stop: fingerprint dedupe + graph wiring.

Fingerprinting is pure and question-agnostic -- these tests never construct a
fingerprint from a question, case id, or domain phrase, matching the module under
test (the deliberate counter-example to sql_shape_validate._EXISTENCE_LIST_ASK_RE).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.eval.sql.agent.anonymizer import SqlAnonymizer
from app.eval.sql.agent.anonymizing_agent import AnonymizingSqlAgent, SqlAgentExecutionError
from app.eval.sql.agent.graph import build_sql_graph
from app.eval.sql.agent.no_progress import fingerprint_sql, observe_sql_execution
from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded
from app.eval.sql.agent.run_outcome import classify_sql_agent_execution_error
from app.eval.sql.diagnostics import _is_budget_exceeded
from tests.eval.sql.agent.test_sql_graph import FakeToolModel, _graph_policy, _sqlite_db

# ------------------------------------------------------------------- fingerprint_sql


def test_whitespace_casing_and_trailing_semicolon_share_a_fingerprint():
    a = fingerprint_sql("SELECT * FROM customers LIMIT 20")
    b = fingerprint_sql("select   *   from customers limit 20;")
    c = fingerprint_sql("  SELECT *  FROM  customers  LIMIT  20  ;  ")
    assert a == b == c


def test_different_limit_literals_do_not_share_a_fingerprint():
    """Genuine refinement (a bigger/smaller page) must not be suppressed as a stall."""
    a = fingerprint_sql("SELECT * FROM customers LIMIT 20")
    b = fingerprint_sql("SELECT * FROM customers LIMIT 50")
    assert a != b


# ------------------------------------------------------------- observe_sql_execution


def test_two_repeats_with_no_new_evidence_do_not_trigger_the_stop():
    seen: dict = {}
    sql = "SELECT id FROM customers LIMIT 20"
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # 1st (establish)
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # repeat 1
    observe_sql_execution(seen, sql, row_count=20, truncated=True)  # repeat 2 -- must not raise


def test_three_repeats_with_no_new_evidence_trigger_the_stop():
    seen: dict = {}
    sql = "SELECT id FROM customers LIMIT 20"
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # 1st (establish)
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # repeat 1
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # repeat 2

    with pytest.raises(SqlContextBudgetExceeded) as ei:
        observe_sql_execution(seen, sql, row_count=20, truncated=True)  # repeat 3 -> stop
    assert ei.value.reason == "stall_no_progress"


def test_changed_row_count_or_truncation_resets_the_streak():
    """New evidence (a different row count or truncation state) is progress -- it must
    reset the streak, not just fail to increment it. Six total executions here, but the
    streak never reaches 3 because evidence changes at execution 4."""
    seen: dict = {}
    sql = "SELECT id FROM customers LIMIT 20"
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # establish
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # repeat 1
    seen = observe_sql_execution(seen, sql, row_count=20, truncated=True)  # repeat 2
    seen = observe_sql_execution(seen, sql, row_count=15, truncated=False)  # NEW evidence -> reset
    seen = observe_sql_execution(seen, sql, row_count=15, truncated=False)  # repeat 1 (new)
    observe_sql_execution(seen, sql, row_count=15, truncated=False)  # repeat 2 -- must not raise


def test_three_distinct_queries_never_trigger_the_stop():
    seen: dict = {}
    seen = observe_sql_execution(seen, "SELECT a FROM t1", row_count=1, truncated=False)
    seen = observe_sql_execution(seen, "SELECT b FROM t2", row_count=2, truncated=False)
    observe_sql_execution(seen, "SELECT c FROM t3", row_count=3, truncated=False)


# --------------------------------------------------------- graph wiring (integration)


def _schema_call(call_id: str = "s"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_schema",
                "args": {"tables": "products"},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _query_call(query: str, call_id: str):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "sql_db_query", "args": {"query": query}, "id": call_id, "type": "tool_call"}
        ],
    )


def test_stall_flows_through_the_wrapper_and_both_classifiers(tmp_path):
    """4 identical sql_db_query executions (same row count, never truncated) must stall
    the graph -- and the resulting error must classify exactly like a real budget stop,
    through the SAME wrapper and classifiers, not a parallel path this module invents.

    The question ("list the products") deliberately matches explicit_multirow (any row
    count is a shape match) and not the existence-list-ask pattern, so the pre-existing
    shape/streak logic never fires first -- the no-progress stop is what ends this run.
    """
    db = _sqlite_db(tmp_path, row_count=3)
    same_sql = "SELECT id, name FROM products ORDER BY id"
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call(same_sql, call_id="q1"),
            _query_call(same_sql, call_id="q2"),
            _query_call(same_sql, call_id="q3"),
            _query_call(same_sql, call_id="q4"),
        ]
    )
    graph = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy())
    anon = SqlAnonymizer("q-stall-no-progress", role="sales", ner_enabled=False)
    agent = AnonymizingSqlAgent(graph, anon)

    with pytest.raises(SqlAgentExecutionError) as ei:
        agent.invoke({"input": "list the products"})
    assert str(ei.value) == "budget_exceeded:stall_no_progress"
    assert isinstance(ei.value.__cause__, SqlContextBudgetExceeded)

    stop = classify_sql_agent_execution_error(ei.value)
    assert stop.kind == "incomplete"
    assert stop.reason == "stall_no_progress"
    assert _is_budget_exceeded(ei.value) is True


def test_three_genuinely_different_queries_finish_normally_through_the_graph(tmp_path):
    """Regression guard: three DIFFERENT queries against the same question must not be
    mistaken for a stall (this is what distinguishes the no-progress stop from a bare
    query-count cap)."""
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SELECT id, name FROM products ORDER BY id LIMIT 1", call_id="q1"),
            _query_call("SELECT id, name FROM products ORDER BY id LIMIT 2", call_id="q2"),
            _query_call("SELECT id, name FROM products ORDER BY id LIMIT 3", call_id="q3"),
            AIMessage(content="Here are the products."),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("list the products")]}
    )
    assert out["messages"][-1].content == "Here are the products."
