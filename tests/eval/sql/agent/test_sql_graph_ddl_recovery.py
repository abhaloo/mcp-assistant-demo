"""Fake-graph tests for illegal-SQL recovery after is_safe_select reject (Phase E4).

Oracle: evals/experiments/sql-post-canary-phase-e-contract.json E4_illegal_ddl_recovery.
"""

from __future__ import annotations

import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.agent import ILLEGAL_SQL_RECOVERY_RULES, SQL_AGENT_PREFIX
from app.eval.sql.agent.clarify import CLARIFY_PREFIX
from app.eval.sql.agent.graph import build_sql_graph
from app.guardrails.sql_guard import is_safe_select
from tests.eval.sql.agent.test_sql_graph import FakeToolModel, _sqlite_db


def _graph_policy():
    return ScopedSqlPolicy(entity_id=1, cross_entity=False)


def _schema_call():
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_schema",
                "args": {"tables": "products"},
                "id": "s",
                "type": "tool_call",
            }
        ],
    )


def _query_call(query: str, call_id: str = "q"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_query",
                "args": {"query": query},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _only_select_error_messages(messages: list) -> list[ToolMessage]:
    return [
        m
        for m in messages
        if isinstance(m, ToolMessage)
        and getattr(m, "name", None) == "sql_db_query"
        and str(m.content or "").startswith("Error: only SELECT")
    ]


def _first_ai_after_error(messages: list) -> AIMessage:
    error_idx = next(
        i
        for i, m in enumerate(messages)
        if isinstance(m, ToolMessage)
        and getattr(m, "name", None) == "sql_db_query"
        and str(m.content or "").startswith("Error:")
    )
    for m in messages[error_idx + 1 :]:
        if isinstance(m, AIMessage):
            return m
    raise AssertionError("no AIMessage after Error ToolMessage")


def _assert_recovery_not_illegal(ai: AIMessage) -> None:
    if ai.tool_calls:
        call = ai.tool_calls[0]
        assert call["name"] == "sql_db_query", call
        query = str(call["args"].get("query", ""))
        assert "SHOW COLUMNS" not in query.upper()
        assert "SHOW TABLES" not in query.upper()
        assert "DROP" not in query.upper()
        assert "ALTER" not in query.upper()
        ok, _ = is_safe_select(query)
        assert ok, query
    else:
        assert str(ai.content or "").startswith(CLARIFY_PREFIX)


def test_sql_agent_prefix_includes_illegal_sql_recovery_rule() -> None:
    assert "TOOL ERROR RECOVERY" in ILLEGAL_SQL_RECOVERY_RULES
    assert "SHOW COLUMNS" in ILLEGAL_SQL_RECOVERY_RULES
    assert "CLARIFY:" in ILLEGAL_SQL_RECOVERY_RULES
    assert ILLEGAL_SQL_RECOVERY_RULES.strip() in SQL_AGENT_PREFIX


@pytest.mark.parametrize(
    "illegal_query",
    [
        "DROP TABLE products",
        "SHOW COLUMNS FROM products",
    ],
)
def test_is_safe_select_still_fail_closed_for_illegal_queries(illegal_query: str) -> None:
    ok, reason = is_safe_select(illegal_query)
    assert not ok
    assert reason


def test_after_only_select_error_next_turn_recovers_with_select(tmp_path) -> None:
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("DROP TABLE products", call_id="bad"),
            _query_call("SELECT COUNT(*) FROM products", call_id="good"),
            AIMessage(content="There is 1 product."),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many products?")]}
    )
    assert _only_select_error_messages(out["messages"])
    recovery = _first_ai_after_error(out["messages"])
    _assert_recovery_not_illegal(recovery)
    assert recovery.tool_calls
    assert recovery.tool_calls[0]["name"] == "sql_db_query"
    assert llm._queue == []


def test_after_only_select_error_next_turn_may_clarify(tmp_path) -> None:
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SHOW COLUMNS FROM products", call_id="bad"),
            AIMessage(content=f"{CLARIFY_PREFIX} Which product column should I count?"),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many products?")]}
    )
    assert _only_select_error_messages(out["messages"])
    recovery = _first_ai_after_error(out["messages"])
    _assert_recovery_not_illegal(recovery)
    assert str(recovery.content or "").startswith(CLARIFY_PREFIX)
    assert not recovery.tool_calls


def test_recovery_turn_not_show_columns_or_drop(tmp_path) -> None:
    """Contract E4: recovery must not repeat SHOW COLUMNS / DDL after Error inject."""
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("DROP TABLE products"),
            _query_call("SELECT id FROM products LIMIT 1"),
            AIMessage(content="Done."),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("list one product id")]}
    )
    recovery = _first_ai_after_error(out["messages"])
    if recovery.tool_calls:
        q = str(recovery.tool_calls[0]["args"].get("query", "")).upper()
        assert not re.search(r"\b(DROP|ALTER|SHOW\s+COLUMNS)\b", q)
    assert llm._queue == []
