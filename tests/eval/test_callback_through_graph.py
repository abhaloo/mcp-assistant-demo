"""STOP-THE-LINE spike: CapturingCallback must still capture SQL when sql_db_query
runs inside a compiled LangGraph (spec §8.2 / R3). Fakes only — no DB, no API."""

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.eval.sql.diagnostics import CapturingCallback


def _one_shot_graph():
    @tool
    def sql_db_query(query: str) -> str:
        """Execute SQL (stub)."""
        return "rows: 1"

    def emit(state: MessagesState):
        call = {
            "name": "sql_db_query",
            "args": {"query": "SELECT 1"},
            "id": "c1",
            "type": "tool_call",
        }
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    g = StateGraph(MessagesState)
    g.add_node("emit", emit)
    g.add_node("run_query", ToolNode([sql_db_query]))
    g.add_edge(START, "emit")
    g.add_edge("emit", "run_query")
    g.add_edge("run_query", END)
    return g.compile()


def test_capturing_callback_fires_for_sql_db_query_in_graph():
    cb = CapturingCallback()
    _one_shot_graph().invoke(
        {"messages": [HumanMessage("q")]},
        {"callbacks": [cb]},
    )
    assert cb.sql_queries == ["SELECT 1"], f"captured: {cb.sql_queries!r}"
    assert cb.final_sql == "SELECT 1", f"final_sql: {cb.final_sql!r}"
