"""ToolNode dual-bind: sql_calendar_windows runs alongside sql_db_query."""

from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.calendar_windows import calendar_windows
from app.eval.sql.agent.graph import build_sql_graph
from tests.eval.sql.agent.test_sql_graph import FakeToolModel, _sqlite_db


def _graph_policy():
    return ScopedSqlPolicy(entity_id=1, cross_entity=False)


def test_generate_query_binds_query_and_calendar_tools(tmp_path):
    db = _sqlite_db(tmp_path)
    schema_call = AIMessage(
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
    gen_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_query",
                "args": {"query": "SELECT COUNT(*) FROM products"},
                "id": "q",
                "type": "tool_call",
            }
        ],
    )
    final = AIMessage(content="There is 1 product.")
    llm = FakeToolModel([schema_call, gen_call, final])
    build_sql_graph(
        db,
        llm,
        system_content="sys",
        row_policy=_graph_policy(),
        today=date(2026, 6, 16),
    ).invoke({"messages": [HumanMessage("how many products?")]})
    # First bind is schema-only; later binds are generate_query dual tools.
    # Prefer any bind that includes calendar.
    names_sets = [{getattr(t, "name", None) for t in tools} for tools in llm.bound_tools]
    assert any({"sql_db_query", "sql_calendar_windows"} <= names for names in names_sets), (
        names_sets
    )


def test_toolnode_executes_calendar_windows(tmp_path):
    db = _sqlite_db(tmp_path)
    today = date(2026, 6, 16)
    schema_call = AIMessage(
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
    cal_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_calendar_windows",
                "args": {},
                "id": "c",
                "type": "tool_call",
            }
        ],
    )
    final = AIMessage(content="ok")
    llm = FakeToolModel([schema_call, cal_call, final])
    out = build_sql_graph(
        db,
        llm,
        system_content="sys",
        row_policy=_graph_policy(),
        today=today,
    ).invoke({"messages": [HumanMessage("jobs this week?")]})
    tool_msgs = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    cal_msgs = [m for m in tool_msgs if "2026-06-16" in str(m.content)]
    assert cal_msgs, [m.content for m in tool_msgs]
    expected = calendar_windows(today)
    assert expected["today"] in str(cal_msgs[0].content)
    assert expected["this_week"]["start"] in str(cal_msgs[0].content)
