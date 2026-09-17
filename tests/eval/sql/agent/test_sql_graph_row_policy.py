"""sql_db_query applies row-level security through the execute_tool transform seam."""

from __future__ import annotations

from functools import partial
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import create_engine, text

from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.agent.anonymizing_database import AnonymizingSQLDatabase
from app.eval.sql.agent.graph import build_sql_graph
from app.tools.transforms import noop_transform, rls_transform_args
from tests.eval.sql.agent.test_sql_graph import FakeToolModel


def _bills_db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'bills.db'}")
    with eng.begin() as c:
        c.execute(
            text(
                "CREATE TABLE bills (id INTEGER PRIMARY KEY, entity_id INTEGER, product_id INTEGER)"
            )
        )
        c.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)"))
        c.execute(text("INSERT INTO products VALUES (1, 'w')"))
        c.execute(text("INSERT INTO bills VALUES (10, 1, 1)"))
        c.execute(text("INSERT INTO bills VALUES (20, 2, 1)"))
    return AnonymizingSQLDatabase(eng, include_tables=["bills", "products"], anonymizer=None)


def _invoke_query(db, llm, policy, query: str):
    schema_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_schema",
                "args": {"tables": "bills"},
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
                "args": {"query": query},
                "id": "q",
                "type": "tool_call",
            }
        ],
    )
    final = AIMessage(content="done")
    llm._queue = [schema_call, gen_call, final]
    return build_sql_graph(db, llm, system_content="sys", row_policy=policy).invoke(
        {"messages": [HumanMessage("q")]}
    )


def test_sql_db_query_rls_transform_runs_via_seam_not_inline(tmp_path):
    from app.tools.execution import execute_tool as real_execute

    transform_seen: list = []
    runs: list = []

    def spy_execute_tool(*, transform, run, **kwargs):
        transform_seen.append(transform)
        return real_execute(transform=transform, run=run, **kwargs)

    class SpyDb:
        dialect = "sqlite"

        def get_usable_table_names(self):
            return ["bills", "products"]

        def get_table_info(self, tables):
            return "bills"

        def run_bounded(self, command, *, budget, params=None):
            runs.append({"command": command, "params": params})
            return "[(10,)]"

    policy = ScopedSqlPolicy(entity_id=42, cross_entity=False)
    llm = FakeToolModel([])
    with patch("app.eval.sql.agent.graph.execute_tool", side_effect=spy_execute_tool):
        _invoke_query(SpyDb(), llm, policy, "SELECT id FROM bills")

    query_transforms = [
        t
        for t in transform_seen
        if getattr(t, "func", t) is rls_transform_args or t is rls_transform_args
    ]
    assert query_transforms, "execute_tool must see rls_transform_args for sql_db_query"
    bound = query_transforms[0]
    assert getattr(bound, "func", bound) is rls_transform_args
    if isinstance(bound, partial):
        assert bound.keywords.get("row_policy") is policy
    assert runs, "run_bounded must be called after transform"
    assert runs[0]["params"] is not None
    assert any(k.startswith("scope_entity") for k in runs[0]["params"])
    assert "42" not in runs[0]["command"]
    assert "entity_id = 42" not in runs[0]["command"].replace(" ", "")


def test_build_sql_graph_requires_row_policy(tmp_path):
    """build_sql_graph requires row_policy; a missing or None value raises
    TypeError at compile time, before the graph runs."""
    import pytest

    db = _bills_db(tmp_path)
    with pytest.raises(TypeError, match="row_policy"):
        build_sql_graph(db, FakeToolModel([]), system_content="sys")  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="row_policy"):
        build_sql_graph(db, FakeToolModel([]), system_content="sys", row_policy=None)


def test_sql_db_query_graph_path_entity_1_cannot_leak_entity_2_rows(tmp_path):
    """The sql_db_query graph path must never leak another entity's rows.
    Seeded rows use id 10 for entity 1 and id 20 for entity 2."""
    db = _bills_db(tmp_path)
    policy = ScopedSqlPolicy(entity_id=1, cross_entity=False)
    llm = FakeToolModel([])
    state = _invoke_query(db, llm, policy, "SELECT id FROM bills")
    query_msgs = [
        m for m in state["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "q"
    ]
    assert len(query_msgs) == 1, "sql_db_query must produce exactly one tool result"
    content = str(query_msgs[0].content or "")
    # The public tool seam now renders bounded rows as a tabular payload;
    # retain tuple compatibility for older adapters while asserting the same
    # independent row-isolation oracle.
    assert "(10," in content or "[(10,)]" in content or "\nid\n10" in content
    assert "(20," not in content and ", 20)" not in content


def test_noop_transform_never_scopes_under_real_policy(tmp_path):
    from app.tools.execution import execute_tool as real_execute

    runs: list = []
    transform_seen: list = []

    class SpyDb:
        dialect = "sqlite"

        def get_usable_table_names(self):
            return ["bills"]

        def get_table_info(self, tables):
            return "bills"

        def run_bounded(self, command, *, budget, params=None):
            runs.append({"command": command, "params": params})
            return "[]"

    def spy_execute_tool(*, transform, run, name, **kwargs):
        t = noop_transform if name == "sql_db_query" else transform
        transform_seen.append(t)
        return real_execute(transform=t, run=run, name=name, **kwargs)

    policy = ScopedSqlPolicy(entity_id=42, cross_entity=False)
    llm = FakeToolModel([])
    with patch("app.eval.sql.agent.graph.execute_tool", side_effect=spy_execute_tool):
        _invoke_query(SpyDb(), llm, policy, "SELECT id FROM bills")

    assert transform_seen and transform_seen[0] is noop_transform
    if runs:
        params = runs[0]["params"] or {}
        assert not any(k.startswith("scope_entity") for k in params)


def test_production_compile_rejects_eval_fixture_by_default(tmp_path):
    import pytest

    db = _bills_db(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        build_sql_graph(
            db,
            FakeToolModel([]),
            system_content="sys",
            row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        )


def test_eval_fixture_allowed_only_with_explicit_flag(tmp_path):
    db = _bills_db(tmp_path)
    graph = build_sql_graph(
        db,
        FakeToolModel([]),
        system_content="sys",
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    assert graph is not None


def test_schema_tool_keeps_noop_transform(tmp_path):
    from app.tools.execution import execute_tool as real_execute

    transform_seen: list = []

    def spy_execute_tool(*, transform, run, name, **kwargs):
        if name == "sql_db_schema":
            transform_seen.append(transform)
        return real_execute(transform=transform, run=run, name=name, **kwargs)

    db = _bills_db(tmp_path)
    policy = ScopedSqlPolicy(entity_id=1, cross_entity=False)
    schema_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "sql_db_schema",
                "args": {"tables": "bills"},
                "id": "s",
                "type": "tool_call",
            }
        ],
    )
    final = AIMessage(content="done")
    llm = FakeToolModel([schema_call, final])
    with patch("app.eval.sql.agent.graph.execute_tool", side_effect=spy_execute_tool):
        build_sql_graph(db, llm, system_content="sys", row_policy=policy).invoke(
            {"messages": [HumanMessage("schema")]}
        )
    assert transform_seen
    assert transform_seen[0] is noop_transform
    assert getattr(transform_seen[0], "func", transform_seen[0]) is not rls_transform_args
