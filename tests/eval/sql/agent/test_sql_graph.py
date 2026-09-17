"""Unit tests for the hand-built LangGraph SQL agent. LLM is faked; DB is sqlite."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import create_engine, text

from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.anonymizing_database import AnonymizingSQLDatabase
from app.eval.sql.agent.clarify import is_clarification_answer
from app.eval.sql.agent.graph import (
    _apply_statement_timeout,
    _business_semantics_error,
    _is_query_timeout,
    build_sql_graph,
)
from app.eval.sql.agent.run_budget import SqlRunBudget
from app.eval.sql.agent.tool_budget import SQL_RESULT_TRUNCATION_MARKER, SqlToolOutputBudget


def _graph_policy():
    return ScopedSqlPolicy(entity_id=1, cross_entity=False)


class FakeToolModel:
    """Fake chat model: returns queued AIMessages; bind_tools is a no-op (returns self)."""

    def __init__(self, queue):
        self._queue = list(queue)
        self.bind_kwargs: list[dict] = []
        self.bound_tools: list[list] = []
        self.invoke_messages: list[list] = []

    def bind_tools(self, tools, **kwargs):
        self.bind_kwargs.append(kwargs)
        self.bound_tools.append(list(tools))
        return self

    def invoke(self, messages, config=None):
        self.invoke_messages.append(list(messages))
        return self._queue.pop(0)


def test_loop_nodes_disable_parallel_tool_calls(tmp_path):
    """Every bind_tools call in the loop must pass parallel_tool_calls=False.

    Without it a model can emit hundreds of duplicate tool_calls in one message.
    ToolNode runs them all, and recursion_limit counts steps, not tool calls per
    message, so nothing else caps the fan-out.
    """
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
    build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many products?")]}
    )
    assert llm.bind_kwargs, "bind_tools was never called"
    assert all(kw.get("parallel_tool_calls") is False for kw in llm.bind_kwargs), llm.bind_kwargs


def test_generate_query_binds_query_and_calendar_not_schema(tmp_path):
    """generate_query binds sql_db_query + sql_calendar_windows — not a
    mid-loop schema re-fetch."""
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
    final = AIMessage(content="Done.")
    llm = FakeToolModel([schema_call, gen_call, final])
    build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many?")]}
    )
    # last bind is generate_query's; must expose query+calendar, not schema.
    tool_names = {t.name for t in llm.bound_tools[-1]}
    assert tool_names == {"sql_db_query", "sql_calendar_windows"}


def _sqlite_db(tmp_path, *, row_count: int = 1):
    eng = create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        for i in range(1, row_count + 1):
            c.execute(text("INSERT INTO products VALUES (:id, :name)"), {"id": i, "name": f"p{i}"})
    return AnonymizingSQLDatabase(eng, include_tables=["products"], anonymizer=None)


def _run_select_all_graph(db, llm, *, budget: SqlToolOutputBudget | None = None):
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
                "args": {"query": "SELECT * FROM products ORDER BY id"},
                "id": "q",
                "type": "tool_call",
            }
        ],
    )
    final = AIMessage(content="Done.")
    llm._queue = [schema_call, gen_call, final]
    kwargs = {"tool_output_budget": budget} if budget is not None else {}
    kwargs.setdefault("row_policy", _graph_policy())
    return build_sql_graph(db, llm, system_content="sys", **kwargs).invoke(
        {"messages": [HumanMessage("list products")]}
    )


def test_graph_routes_generate_run_then_answers(tmp_path):
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

    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many products?")]}
    )
    assert any(
        isinstance(m, AIMessage) and not m.tool_calls and "1 product" in (m.content or "")
        for m in out["messages"]
    )


def test_graph_exposes_five_nodes(tmp_path):
    graph = build_sql_graph(
        _sqlite_db(tmp_path), FakeToolModel([]), system_content="sys", row_policy=_graph_policy()
    )
    assert set(graph.get_graph().nodes) >= {
        "list_tables",
        "call_get_schema",
        "get_schema",
        "preinject_metric_definition",
        "generate_query",
        "run_query",
    }
    assert "check_query" not in graph.get_graph().nodes


def test_statement_timeout_config_default():
    from app.config import Settings

    s = Settings(redaction_hmac_key="test-key", rag_jwt_secret="test-secret")
    assert s.sql_agent_statement_timeout_ms == 15000


def test_apply_statement_timeout_injects_mysql_hint():
    q = "SELECT * FROM bills"
    out = _apply_statement_timeout(q, 15000, "mysql")
    assert "MAX_EXECUTION_TIME(15000)" in out
    assert out.startswith("SELECT /*+")


def test_apply_statement_timeout_skips_non_mysql():
    q = "SELECT 1"
    assert _apply_statement_timeout(q, 15000, "sqlite") == q


def test_apply_statement_timeout_leaves_cte_unchanged():
    """MySQL ignores an optimizer hint placed before WITH. A CTE therefore passes
    through un-capped rather than carrying a hint that has no effect."""
    q = "WITH x AS (SELECT 1) SELECT * FROM x"
    assert _apply_statement_timeout(q, 15000, "mysql") == q


def test_high_priority_query_requires_ui_department_exclusion():
    missing = "SELECT COUNT(*) FROM work_orders WHERE priority = 'high'"
    correct = "SELECT COUNT(*) FROM work_orders WHERE priority = 'high' AND department_id <> 12"

    assert "department_id <> 12" in (_business_semantics_error(missing) or "")
    assert _business_semantics_error(correct) is None


def test_high_priority_semantic_error_is_recoverable_by_the_query_loop(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'work-orders.db'}")
    with eng.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE work_orders "
                "(id INTEGER PRIMARY KEY, priority TEXT, department_id INTEGER)"
            )
        )
        connection.execute(
            text("INSERT INTO work_orders VALUES (1, 'high', 2), (2, 'high', 12), (3, 'normal', 2)")
        )
    db = AnonymizingSQLDatabase(
        eng,
        include_tables=["work_orders"],
        anonymizer=None,
    )
    schema_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_schema",
                "args": {"tables": "work_orders"},
                "id": "schema",
                "type": "tool_call",
            }
        ],
    )
    wrong_query = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_query",
                "args": {"query": "SELECT COUNT(*) FROM work_orders WHERE priority = 'high'"},
                "id": "wrong",
                "type": "tool_call",
            }
        ],
    )
    corrected_query = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_query",
                "args": {
                    "query": (
                        "SELECT COUNT(*) FROM work_orders WHERE priority = 'high' "
                        "AND department_id <> 12"
                    )
                },
                "id": "corrected",
                "type": "tool_call",
            }
        ],
    )
    llm = FakeToolModel(
        [schema_call, wrong_query, corrected_query, AIMessage(content="There is 1 job.")]
    )

    out = build_sql_graph(
        db,
        llm,
        system_content="sys",
        # cross_entity: this fixture has no entity_id column — isolate semantics loop
        row_policy=ScopedSqlPolicy(entity_id=1, cross_entity=True),
    ).invoke({"messages": [HumanMessage("How many high priority jobs are there?")]})

    tool_messages = [
        message.content for message in out["messages"] if isinstance(message, ToolMessage)
    ]
    assert any("department_id <> 12" in content for content in tool_messages)
    # Header-aware formatter may emit "COUNT(*)\n1" instead of "[(1,)]".
    assert any(
        "[(1,)]" in content or content.rstrip().endswith("\n1") or content.rstrip() == "1"
        for content in tool_messages
    )


def test_is_query_timeout_detects_mysql_error():
    assert _is_query_timeout(Exception("(3024, 'Query execution was interrupted')"))
    assert _is_query_timeout(Exception("maximum statement execution time exceeded"))
    assert not _is_query_timeout(Exception("syntax error"))


def test_sql_db_query_respects_row_budget(tmp_path):
    db = _sqlite_db(tmp_path, row_count=40)
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    llm = FakeToolModel([])
    out = _run_select_all_graph(db, llm, budget=budget)

    query_msgs = [
        m.content
        for m in out["messages"]
        if isinstance(m, ToolMessage) and SQL_RESULT_TRUNCATION_MARKER in (m.content or "")
    ]
    assert query_msgs, "expected a bounded sql_db_query ToolMessage"
    content = query_msgs[0]
    assert content.count("), (") <= 19  # at most 20 rows → 19 inter-row separators
    assert SQL_RESULT_TRUNCATION_MARKER in content


def test_luna_and_deepseek_receive_identical_bounded_tool_message(tmp_path):
    db = _sqlite_db(tmp_path, row_count=40)
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)

    def query_tool_content(llm_label: str) -> str:
        llm = FakeToolModel([])
        llm.label = llm_label  # noqa: B010 — distinguish twin invocations in debug
        out = _run_select_all_graph(db, llm, budget=budget)
        for m in out["messages"]:
            if isinstance(m, ToolMessage) and SQL_RESULT_TRUNCATION_MARKER in (m.content or ""):
                return m.content
        raise AssertionError(f"{llm_label}: no bounded query ToolMessage")

    assert query_tool_content("luna") == query_tool_content("deepseek")


def test_long_query_surfaces_timeout_as_recoverable_error(tmp_path, monkeypatch):
    db = _sqlite_db(tmp_path)

    def _timeout_run(_query, *, budget=None, params=None):
        # MySQL ER_QUERY_TIMEOUT (3024) — the message _is_query_timeout matches on.
        raise Exception("(3024, 'maximum statement execution time exceeded')")

    monkeypatch.setattr(db, "run_bounded", _timeout_run)
    monkeypatch.setattr("app.eval.sql.agent.graph.settings.sql_agent_statement_timeout_ms", 5000)

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

    out = build_sql_graph(db, llm, system_content="sys", row_policy=_graph_policy()).invoke(
        {"messages": [HumanMessage("how many?")]}
    )
    timeout_msgs = [
        m for m in out["messages"] if isinstance(m, ToolMessage) and "exceeded 5000ms" in m.content
    ]
    assert timeout_msgs


def test_schema_or_generate_emits_clarify_when_run_budget_max_llm_calls(tmp_path):
    llm = FakeToolModel([])
    budget = SqlRunBudget(max_llm_calls=0, max_next_prompt_tokens=1_000_000)
    graph = build_sql_graph(
        _sqlite_db(tmp_path), llm, "sys", run_budget=budget, row_policy=_graph_policy()
    )
    out = graph.invoke({"messages": [HumanMessage("how many?")]})
    last = out["messages"][-1]
    assert isinstance(last, AIMessage)
    assert is_clarification_answer(str(last.content))
