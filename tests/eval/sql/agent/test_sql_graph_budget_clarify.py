"""Graph: max_llm_calls → CLARIFY AIMessage; other budget reasons still raise."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.eval.sql.agent.clarify import is_clarification_answer
from app.eval.sql.agent.graph import build_sql_graph
from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded, SqlRunBudget

# Import the same locals tip tests already use:
from tests.eval.sql.agent.test_sql_graph import FakeToolModel, _graph_policy, _sqlite_db


def test_generate_query_emits_clarify_when_max_llm_calls_hit(tmp_path):
    """Cap hit must surface CLARIFY — not raise budget_exceeded."""
    llm = FakeToolModel([])  # empty queue — any real LLM invoke would blow up
    budget = SqlRunBudget(max_llm_calls=0, max_next_prompt_tokens=1_000_000)
    graph = build_sql_graph(
        _sqlite_db(tmp_path), llm, "sys", run_budget=budget, row_policy=_graph_policy()
    )
    result = graph.invoke({"messages": [HumanMessage("how many open invoices?")]})
    last = result["messages"][-1]
    assert isinstance(last, AIMessage)
    assert is_clarification_answer(str(last.content))
    assert "rephrase" not in str(last.content).lower()


def test_next_prompt_tokens_still_raises(tmp_path):
    llm = FakeToolModel([])
    budget = SqlRunBudget(max_llm_calls=100, max_next_prompt_tokens=1)
    graph = build_sql_graph(
        _sqlite_db(tmp_path), llm, "sys", run_budget=budget, row_policy=_graph_policy()
    )
    with pytest.raises(SqlContextBudgetExceeded) as ei:
        graph.invoke({"messages": [HumanMessage("x" * 20_000)]})
    assert ei.value.reason == "next_prompt_tokens"


def test_call_get_schema_clarify_does_not_enter_get_schema_toolnode(tmp_path, monkeypatch):
    """Budget CLARIFY from call_get_schema must END — not enter ToolNode(get_schema)."""
    entered = {"get_schema": False}
    from langgraph.prebuilt import ToolNode

    original_init = ToolNode.__init__

    def tracking_init(self, tools, *args, **kwargs):
        original_init(self, tools, *args, **kwargs)
        orig_invoke = self.invoke

        def wrapped(state, config=None):
            entered["get_schema"] = True
            return orig_invoke(state, config)

        self.invoke = wrapped

    monkeypatch.setattr(ToolNode, "__init__", tracking_init)

    llm = FakeToolModel([])  # any real LLM invoke would blow up
    budget = SqlRunBudget(max_llm_calls=0, max_next_prompt_tokens=1_000_000)
    graph = build_sql_graph(
        _sqlite_db(tmp_path), llm, "sys", run_budget=budget, row_policy=_graph_policy()
    )
    result = graph.invoke({"messages": [HumanMessage("how many open invoices?")]})
    last = result["messages"][-1]
    assert isinstance(last, AIMessage)
    assert is_clarification_answer(str(last.content))
    assert entered["get_schema"] is False, "get_schema ToolNode must not run after budget CLARIFY"
