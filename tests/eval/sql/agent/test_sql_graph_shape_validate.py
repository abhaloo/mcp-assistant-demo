"""Fake-graph producer tests for post-exec validate_result_shape."""

from __future__ import annotations

import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.anonymizing_agent import SqlAgentNoAnswer, _final_answer
from app.eval.sql.agent.clarify import CLARIFY_PREFIX
from app.eval.sql.agent.graph import build_sql_graph
from app.eval.sql.agent.shape_validate import SHAPE_MISMATCH_TOOL_NAME
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


def _calendar_call(call_id: str = "c"):
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_calendar_windows",
                "args": {},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _shape_feedback_messages(messages: list) -> list[ToolMessage]:
    return [
        m
        for m in messages
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == SHAPE_MISMATCH_TOOL_NAME
    ]


def _assert_teaching_payload(msg: ToolMessage, *, expected_grain: str, observed_row_count: int):
    payload = json.loads(str(msg.content))
    assert payload["kind"] == "shape_mismatch"
    assert payload["reason"] == "row_count_mismatch"
    assert payload["expected_grain"] == expected_grain
    assert payload["observed_row_count"] == observed_row_count
    assert payload["shape_retry_index"] == 0


def _assert_shape_feedback_pair(
    messages: list,
    *,
    expected_grain: str,
    observed_row_count: int,
):
    feedback = _shape_feedback_messages(messages)
    assert len(feedback) == 1
    tool_msg = feedback[0]
    _assert_teaching_payload(
        tool_msg,
        expected_grain=expected_grain,
        observed_row_count=observed_row_count,
    )
    assert tool_msg.name == SHAPE_MISMATCH_TOOL_NAME
    assert tool_msg.name != "sql_db_query"

    idx = messages.index(tool_msg)
    assert idx > 0
    ai_msg = messages[idx - 1]
    assert isinstance(ai_msg, AIMessage)
    assert ai_msg.tool_calls
    call = ai_msg.tool_calls[0]
    assert call["id"] == tool_msg.tool_call_id
    assert call["name"] == SHAPE_MISMATCH_TOOL_NAME
    assert call["name"] != "sql_db_query"


def test_graph_has_validate_result_shape_node(tmp_path):
    graph = build_sql_graph(
        _sqlite_db(tmp_path),
        FakeToolModel([]),
        system_content="sys",
        row_policy=_graph_policy(),
    )
    assert "validate_result_shape" in graph.get_graph().nodes


def test_count_question_multirow_forces_immediate_clarify(tmp_path):
    """count_scalar shape mismatch → CLARIFY with no teaching retry (budget hygiene)."""
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SELECT id, name FROM products ORDER BY id"),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many invoices have been fully paid?")]}
    )
    assert _shape_feedback_messages(out["messages"]) == []
    clarify_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert len(clarify_msgs) == 1
    assert "shape mismatch" in str(clarify_msgs[0].content).lower()
    assert _final_answer(out["messages"]).startswith(CLARIFY_PREFIX)


def test_singular_multirow_triggers_retry_teaching_signal(tmp_path):
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SELECT id, name FROM products ORDER BY id"),
            _query_call("SELECT name FROM products ORDER BY id LIMIT 1", call_id="q2"),
            AIMessage(content="The top customer is Acme."),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("who is our top customer by invoice value?")]}
    )
    _assert_shape_feedback_pair(
        out["messages"],
        expected_grain="singular",
        observed_row_count=3,
    )
    assert out.get("shape_retries", 0) == 0
    assert llm._queue == []


def test_calendar_only_hop_passes_through_without_shape_fail(tmp_path):
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [_schema_call(), _calendar_call(), AIMessage(content="This week starts Monday.")]
    )
    out = build_sql_graph(
        db,
        llm,
        system_content="sys",
        row_policy=_graph_policy(),
        today=date(2026, 6, 16),
    ).invoke({"messages": [HumanMessage("jobs this week?")]})
    assert _shape_feedback_messages(out["messages"]) == []
    clarify = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert clarify == []


def test_error_tool_message_skips_shape_without_retry_storm(tmp_path):
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("DROP TABLE products"),
            AIMessage(content="I cannot run that query."),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many products?")]}
    )
    assert _shape_feedback_messages(out["messages"]) == []
    error_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, ToolMessage)
        and getattr(m, "name", None) == "sql_db_query"
        and str(m.content or "").startswith("Error:")
    ]
    assert error_msgs
    clarify = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert clarify == []


def test_stale_multirow_then_latest_one_row_passes_shape(tmp_path):
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SELECT id, name FROM products ORDER BY id"),
            _query_call("SELECT name FROM products ORDER BY id LIMIT 1", call_id="q2"),
            AIMessage(content="The top customer is Acme."),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("who is our top customer by invoice value?")]}
    )
    _assert_shape_feedback_pair(
        out["messages"],
        expected_grain="singular",
        observed_row_count=3,
    )
    query_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "sql_db_query"
    ]
    assert len(query_msgs) >= 2
    clarify = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert clarify == []
    assert out.get("shape_retries", 0) == 0


def test_first_human_grain_ignores_clarify_reply(tmp_path):
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _query_call("SELECT id, name FROM products ORDER BY id"),
            AIMessage(content="The top customer is Acme."),
        ]
    )
    prior = [
        HumanMessage("who is our top customer by sales amount?"),
        AIMessage(content=f"{CLARIFY_PREFIX} which region?"),
        HumanMessage("list all customers in Calgary"),
    ]
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": prior}
    )
    _assert_shape_feedback_pair(
        out["messages"],
        expected_grain="singular",
        observed_row_count=3,
    )


def test_shape_retry_exhaustion_emits_clarify_for_final_answer(tmp_path):
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SELECT id, name FROM products ORDER BY id"),
            _query_call("SELECT id, name FROM products ORDER BY id", call_id="q2"),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("who is our top customer by invoice value?")]}
    )
    clarify_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert len(clarify_msgs) == 1
    assert "shape mismatch" in str(clarify_msgs[0].content).lower()
    answer = _final_answer(out["messages"])
    assert answer.startswith(CLARIFY_PREFIX)


def test_existence_list_streak_forces_count_vs_list_clarify(tmp_path):
    """Two consecutive anti-join LIMIT-N list hops on existence Ask → CLARIFY."""
    # Self anti-join on products fixture so ToolNode returns rows (not error).
    list_sql = (
        "SELECT p.id, p.name FROM products p "
        "LEFT JOIN products other ON other.id = p.id AND other.id < 0 "
        "WHERE other.id IS NULL ORDER BY p.id LIMIT 20"
    )
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call(list_sql, call_id="q1"),
            _query_call(list_sql, call_id="q2"),
            _query_call(list_sql, call_id="q3"),
            AIMessage(content="should not reach"),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("which customers have never placed an order?")]}
    )
    clarify_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert len(clarify_msgs) == 1
    body = str(clarify_msgs[0].content).lower()
    assert "count" in body and "list" in body
    assert "shape mismatch" not in body
    assert len(llm._queue) >= 1
    assert _final_answer(out["messages"]).startswith(CLARIFY_PREFIX)


def test_bare_limit_list_does_not_trip_existence_streak(tmp_path):
    """Non-anti-join LIMIT-N probes must not false-trigger streak CLARIFY."""
    list_sql = "SELECT id, name FROM products ORDER BY id LIMIT 20"
    db = _sqlite_db(tmp_path, row_count=3)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call(list_sql, call_id="q1"),
            _query_call(list_sql, call_id="q2"),
            AIMessage(content="Found a few rows."),
        ]
    )
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("which customers have never placed an order?")]}
    )
    clarify_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert clarify_msgs == []
    assert _final_answer(out["messages"]) == "Found a few rows."
    with pytest.raises(SqlAgentNoAnswer):
        _final_answer(out["messages"][:-1])
