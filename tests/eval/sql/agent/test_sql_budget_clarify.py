from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.eval.sql.agent.budget_clarify import (
    BUDGET_CLARIFY_STOP_REASON,
    build_budget_clarify_message,
    build_budget_clarify_question,
)
from app.eval.sql.agent.clarify import CLARIFY_PREFIX, is_clarification_answer


def test_budget_clarify_stop_reason_literal():
    assert BUDGET_CLARIFY_STOP_REASON == "budget_clarify"


def test_grain_mismatch_asks_about_result_shape():
    messages = [
        HumanMessage("how many open invoices?"),
        AIMessage(
            content="",
            tool_calls=[{"name": "sql_db_query", "id": "1", "args": {"query": "SELECT 1"}}],
        ),
        ToolMessage(
            content="col\na\nb\nc\n",  # 3 data rows after header — multirow vs count
            tool_call_id="1",
            name="sql_db_query",
        ),
    ]
    q = build_budget_clarify_question(messages)
    assert "one number" in q.lower() or "count" in q.lower() or "how many" in q.lower()
    assert "rephrase" not in q.lower()


def test_existence_list_loop_asks_count_vs_list_not_generic_metric():
    """cust-no-orders-shaped Ask + LIMIT-N list → count-vs-list, not date/ledger fallback."""
    list_sql = (
        "SELECT c.id, c.name FROM customers c "
        "LEFT JOIN customer_orders co ON co.customer_id = c.id "
        "WHERE co.id IS NULL ORDER BY c.id LIMIT 20"
    )
    messages = [
        HumanMessage("which customers have never placed an order?"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "sql_db_query",
                    "id": "1",
                    "args": {"query": list_sql},
                }
            ],
        ),
        ToolMessage(
            content="id\tname\n1\tA\n2\tB\n3\tC\n",
            tool_call_id="1",
            name="sql_db_query",
        ),
    ]
    q = build_budget_clarify_question(messages)
    assert "count" in q.lower()
    assert "list" in q.lower()
    assert "ledger" not in q.lower()
    assert "date range" not in q.lower()


def test_fallback_is_concrete_not_vague_rephrase():
    messages = [HumanMessage("show me the weird finance thing from last quarter")]
    q = build_budget_clarify_question(messages)
    assert not q.lower().startswith("please rephrase")
    assert "rephrase" not in q.lower()
    assert len(q) > 20


def test_message_uses_shared_clarify_marker():
    msg = build_budget_clarify_message([HumanMessage("top customer?")])
    assert is_clarification_answer(str(msg.content))
    assert str(msg.content).lstrip().startswith(CLARIFY_PREFIX)
    assert not getattr(msg, "tool_calls", None)
