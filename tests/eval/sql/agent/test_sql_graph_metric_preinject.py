"""Fake-graph producer tests for metric definition pre-inject."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.anonymizing_agent import _final_answer
from app.eval.sql.agent.clarify import CLARIFY_PREFIX
from app.eval.sql.agent.graph import build_sql_graph
from app.eval.sql.agent.metric_lookup import METRIC_INJECT_MARKER
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


def _system_content_from_invoke(messages: list) -> str | None:
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            return str(msg.get("content") or "")
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if role == "system":
            return str(getattr(msg, "content", "") or "")
    return None


def _generate_query_system_content(llm: FakeToolModel) -> str:
    """Last LLM invoke with a system message is generate_query (after schema hop)."""
    system_payloads = [
        content
        for messages in llm.invoke_messages
        if (content := _system_content_from_invoke(messages)) is not None
    ]
    assert system_payloads, "generate_query never invoked with system content"
    return system_payloads[-1]


def test_graph_has_preinject_metric_definition_node(tmp_path):
    graph = build_sql_graph(
        _sqlite_db(tmp_path),
        FakeToolModel([]),
        system_content="sys",
        row_policy=_graph_policy(),
    )
    assert "preinject_metric_definition" in graph.get_graph().nodes


def test_metric_intent_question_pre_injects_definition_before_generate_query(tmp_path):
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SELECT COUNT(*) FROM products"),
            AIMessage(content="There are 0 fully paid invoices."),
        ]
    )
    build_sql_graph(db, llm, system_content="BASE_SYS", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many invoices are fully paid?")]}
    )

    system_content = _generate_query_system_content(llm)
    assert "BASE_SYS" in system_content
    assert METRIC_INJECT_MARKER in system_content
    assert "fully_paid" in system_content
    assert "remainder" in system_content.lower()

    tool_names = {t.name for t in llm.bound_tools[-1]}
    assert tool_names == {"sql_db_query", "sql_calendar_windows"}


def test_non_metric_question_leaves_system_content_unchanged(tmp_path):
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _schema_call(),
            _query_call("SELECT COUNT(*) FROM products"),
            AIMessage(content="There is 1 product."),
        ]
    )
    build_sql_graph(db, llm, system_content="BASE_SYS", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many products?")]}
    )

    system_content = _generate_query_system_content(llm)
    assert system_content == "BASE_SYS"
    assert METRIC_INJECT_MARKER not in system_content

    tool_names = {t.name for t in llm.bound_tools[-1]}
    assert tool_names == {"sql_db_query", "sql_calendar_windows"}


def test_ambiguous_revenue_emits_clarify_without_sql_tool_calls(tmp_path):
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel([_schema_call()])  # schema hop only — generate_query must not run
    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("show me revenue")]}
    )

    clarify_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert len(clarify_msgs) == 1
    assert "revenue" in str(clarify_msgs[0].content).lower()
    assert len(llm.invoke_messages) == 1  # call_get_schema only
    query_tool_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "sql_db_query"
    ]
    assert query_tool_msgs == []
    answer = _final_answer(out["messages"])
    assert answer.startswith(CLARIFY_PREFIX)


def test_clarify_continuation_runs_preinject_before_generate_query(tmp_path):
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _query_call("SELECT COUNT(*) FROM products"),
            AIMessage(content="There are 0 fully paid invoices."),
        ]
    )
    prior = [
        HumanMessage("how many invoices are fully paid?"),
        AIMessage(content=f"{CLARIFY_PREFIX} which month?"),
        HumanMessage("April 2026"),
    ]
    build_sql_graph(db, llm, system_content="BASE_SYS", row_policy=_graph_policy()).invoke(
        {"messages": prior}
    )

    system_content = _generate_query_system_content(llm)
    assert METRIC_INJECT_MARKER in system_content
    assert "fully_paid" in system_content


def test_revenue_clarify_continuation_resolves_last_human_reply(tmp_path):
    db = _sqlite_db(tmp_path)
    llm = FakeToolModel(
        [
            _query_call("SELECT SUM(price * quantity) FROM bill_items"),
            AIMessage(content="Invoiced sales total is 0."),
        ]
    )
    prior = [
        HumanMessage("show me revenue"),
        AIMessage(
            content=(
                f"{CLARIFY_PREFIX} Which billing metric do you mean? For revenue, "
                "specify invoiced sales vs ledger/accounting revenue."
            )
        ),
        HumanMessage("invoiced sales"),
    ]
    out = build_sql_graph(db, llm, system_content="BASE_SYS", row_policy=_graph_policy()).invoke(
        {"messages": prior}
    )

    clarify_msgs = [
        m
        for m in out["messages"]
        if isinstance(m, AIMessage)
        and not m.tool_calls
        and str(m.content or "").startswith(CLARIFY_PREFIX)
    ]
    assert len(clarify_msgs) == 1  # original CLARIFY only — no second CLARIFY

    system_content = _generate_query_system_content(llm)
    assert METRIC_INJECT_MARKER in system_content
    assert "revenue_invoiced" in system_content
    assert "invoiced" in system_content.lower()
