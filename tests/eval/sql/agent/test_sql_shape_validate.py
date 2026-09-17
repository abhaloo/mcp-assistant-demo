"""Post-canary shape grain oracle tests.

Target module:
  app.rag/chains/sql_shape_validate.py

Public API:
  expected_result_grain(question: str) -> Literal[
    "count_scalar", "singular", "explicit_multirow"
  ] | None
  check_result_shape(*, expected_grain, row_count: int) -> ShapeOk | ShapeMismatch
    ShapeMismatch.reason == "row_count_mismatch" for scalar/singular + row_count >= 2

Oracle: evals/experiments/sql-post-canary-shape-contract.json grain_fixtures_hand_labeled
  (hand labels — never the implementation under test).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage, convert_to_openai_messages

from app.eval.sql.agent.shape_validate import (
    SHAPE_MISMATCH_TOOL_NAME,
    ShapeMismatch,
    ShapeOk,
    build_shape_mismatch_feedback,
    check_result_shape,
    expected_result_grain,
    is_existence_anti_join_list_sql,
    is_limit_n_list_sql,
    parse_sql_tool_result,
)
from app.eval.sql.agent.tool_budget import (
    SQL_RESULT_TRUNCATION_MARKER,
    SqlToolOutputBudget,
    format_sql_tool_result,
)
from app.rag.model_router import needs_cardinality_rules

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4]
    / "evals"
    / "experiments"
    / "sql-post-canary-shape-contract.json"
)
_CONTRACT = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
_GRAIN_FIXTURES: list[tuple[str, str]] = [
    (entry["question"], entry["grain"]) for entry in _CONTRACT["grain_fixtures_hand_labeled"]
]


@pytest.mark.parametrize("question,expected_grain", _GRAIN_FIXTURES)
def test_expected_result_grain_matches_contract_oracle(question, expected_grain):
    assert expected_result_grain(question) == expected_grain


@pytest.mark.parametrize(
    "question",
    [
        "how many invoices have been fully paid?",
        "what is the number of open customer orders?",
    ],
)
def test_count_questions_map_to_count_scalar(question):
    assert expected_result_grain(question) == "count_scalar"


@pytest.mark.parametrize(
    "question",
    [
        "who is our top customer by invoice value?",
        "what was the largest invoice this month and from who",
        "what is the largest pending quotation and for whom?",
        "which customer has the biggest outstanding balance?",
    ],
)
def test_largest_biggest_which_customer_map_to_singular_not_list(question):
    grain = expected_result_grain(question)
    assert grain == "singular"
    assert grain != "explicit_multirow"


@pytest.mark.parametrize(
    "question",
    [
        "who are our top 5 customers by revenue this year?",
        "list overdue invoices",
        "list all open orders",
        "top 10 invoices by value",
    ],
)
def test_explicit_list_and_top_n_map_to_explicit_multirow(question):
    assert expected_result_grain(question) == "explicit_multirow"


@pytest.mark.parametrize("expected_grain", ["count_scalar", "singular"])
@pytest.mark.parametrize("row_count", [2, 3, 10])
def test_scalar_grain_with_multirow_result_is_row_count_mismatch(expected_grain, row_count):
    result = check_result_shape(expected_grain=expected_grain, row_count=row_count)
    assert isinstance(result, ShapeMismatch)
    assert result.reason == "row_count_mismatch"


@pytest.mark.parametrize("expected_grain", ["count_scalar", "singular"])
@pytest.mark.parametrize("row_count", [0, 1])
def test_scalar_grain_with_zero_or_one_row_is_ok(expected_grain, row_count):
    result = check_result_shape(expected_grain=expected_grain, row_count=row_count)
    assert isinstance(result, ShapeOk)


@pytest.mark.parametrize("row_count", [0, 1])
def test_explicit_multirow_with_zero_or_one_row_is_ok_not_fail_closed(row_count):
    """Contract: no list min row_count>=2 fail-closed."""
    result = check_result_shape(expected_grain="explicit_multirow", row_count=row_count)
    assert isinstance(result, ShapeOk)


def test_explicit_multirow_with_many_rows_is_ok():
    result = check_result_shape(expected_grain="explicit_multirow", row_count=5)
    assert isinstance(result, ShapeOk)


# Grain source must be the first HumanMessage (original Ask), not a later
# clarify user_reply. Graph-level proof lives in test_sql_graph_shape_validate.py;
# this pure unit documents the seam: pass first-Human text only.
_FIRST_HUMAN_SINGULAR = "who is our top customer by invoice value?"
_CLARIFY_REPLY_WOULD_BE_LIST = "list all customers in Calgary"


def test_grain_from_first_human_string_not_clarify_reply():
    assert expected_result_grain(_FIRST_HUMAN_SINGULAR) == "singular"
    # If the clarify reply were passed instead, grain would flip to explicit_multirow.
    assert expected_result_grain(_CLARIFY_REPLY_WOULD_BE_LIST) == "explicit_multirow"


def test_p0_cardinality_positives_align_with_shape_grain():
    """Overlap with tests/rag/test_cardinality_rules.py positives."""
    positives = [
        q
        for q, needs in [
            ("who is our top customer by invoice value?", True),
            ("what is the largest pending quotation and for whom?", True),
            ("what was the largest invoice this month and from who", True),
            ("which customer has the biggest outstanding balance?", True),
            ("how many invoices have been fully paid?", True),
            ("what is the number of open customer orders?", True),
        ]
        if needs_cardinality_rules(q)
    ]
    for question in positives:
        grain = expected_result_grain(question)
        assert grain in {"count_scalar", "singular"}, (
            f"P0 cardinality positive {question!r} must not map to explicit_multirow, got {grain!r}"
        )


def test_p0_cardinality_negatives_in_contract_map_to_explicit_multirow():
    """Overlap with tests/rag/test_cardinality_rules.py negatives present in contract."""
    negatives_in_contract = [
        "list overdue invoices",
        "list all open orders",
        "who are our top 5 customers by revenue this year?",
        "top 10 invoices by value",
    ]
    for question in negatives_in_contract:
        assert needs_cardinality_rules(question) is False
        assert expected_result_grain(question) == "explicit_multirow"


def test_parse_empty_string_is_empty_outcome():
    parsed = parse_sql_tool_result("")
    assert parsed.outcome == "empty"
    assert parsed.row_count == 0


def test_parse_error_prefix_is_error_outcome():
    parsed = parse_sql_tool_result("Error: query references out-of-tier table(s): secrets")
    assert parsed.outcome == "error"


def test_parse_header_tsv_counts_data_rows():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    content = format_sql_tool_result(rows, budget=budget, truncated_by_row_cap=False)
    parsed = parse_sql_tool_result(content)
    assert parsed.outcome == "rows"
    assert parsed.row_count == 3


def test_parse_header_only_tsv_is_zero_rows():
    parsed = parse_sql_tool_result("id\tname")
    assert parsed.outcome == "rows"
    assert parsed.row_count == 0


def test_parse_legacy_bare_tuple_fallback():
    parsed = parse_sql_tool_result("[(1,), (2,)]")
    assert parsed.outcome == "rows"
    assert parsed.row_count == 2


def test_shape_mismatch_feedback_returns_adjacent_ai_tool_pair():
    ai_msg, tool_msg = build_shape_mismatch_feedback(
        expected_grain="count_scalar",
        observed_row_count=3,
        shape_retry_index=0,
    )
    assert isinstance(ai_msg, AIMessage)
    assert isinstance(tool_msg, ToolMessage)
    assert ai_msg.tool_calls
    call = ai_msg.tool_calls[0]
    assert call["id"] == tool_msg.tool_call_id == "shape_mismatch_0"
    assert call["name"] == tool_msg.name == SHAPE_MISMATCH_TOOL_NAME
    assert call["name"] != "sql_db_query"

    payload = json.loads(str(tool_msg.content))
    assert payload["kind"] == "shape_mismatch"
    assert payload["reason"] == "row_count_mismatch"
    assert payload["expected_grain"] == "count_scalar"
    assert payload["observed_row_count"] == 3
    assert payload["shape_retry_index"] == 0
    assert call["args"] == payload


def test_shape_mismatch_feedback_openai_wire_no_orphan_tool():
    ai_msg, tool_msg = build_shape_mismatch_feedback(
        expected_grain="singular",
        observed_row_count=2,
        shape_retry_index=1,
    )
    oai = convert_to_openai_messages([ai_msg, tool_msg])
    tool_msgs = [m for m in oai if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "shape_mismatch_1"
    assert tool_msgs[0]["name"] == SHAPE_MISMATCH_TOOL_NAME
    assistant_with_tools = [m for m in oai if m.get("role") == "assistant" and m.get("tool_calls")]
    assert len(assistant_with_tools) == 1
    assert assistant_with_tools[0]["tool_calls"][0]["id"] == "shape_mismatch_1"


def test_parse_truncation_marker_stripped_before_row_count():
    budget = SqlToolOutputBudget(max_rows=20, max_chars=12_000)
    base = format_sql_tool_result([{"id": 1}, {"id": 2}], budget=budget, truncated_by_row_cap=False)
    content = base + "\n" + SQL_RESULT_TRUNCATION_MARKER
    parsed = parse_sql_tool_result(content)
    assert parsed.outcome == "rows"
    assert parsed.row_count == 2


def test_existence_gate_fires_on_query_shape_across_phrasings():
    """A rule keyed to one gold question's phrasing is overfitting with a regex for a spec."""
    anti_join = (
        "SELECT c.id FROM customers c LEFT JOIN customer_orders co "
        "ON co.customer_id = c.id WHERE co.id IS NULL LIMIT 20"
    )
    assert is_existence_anti_join_list_sql(anti_join)
    assert not is_existence_anti_join_list_sql("SELECT c.id FROM customers c LIMIT 10")


def test_the_phrase_keyed_regex_is_gone():
    import app.eval.sql.agent.shape_validate as shape_validate

    assert not hasattr(shape_validate, "_EXISTENCE_LIST_ASK_RE")


def test_limit_n_list_sql_detects_capped_list_not_count():
    list_sql = (
        "SELECT c.id FROM customers c LEFT JOIN customer_orders co "
        "ON co.customer_id = c.id WHERE co.id IS NULL LIMIT 20"
    )
    assert is_limit_n_list_sql(list_sql)
    assert is_existence_anti_join_list_sql(list_sql)
    assert not is_limit_n_list_sql("SELECT COUNT(*) FROM customers")
    assert not is_existence_anti_join_list_sql("SELECT id, name FROM products ORDER BY id LIMIT 20")


def test_every_shape_gate_path_reports_all_four_state_keys():
    """A missing key silently means 'unchanged' in LangGraph state merging, so
    a partial return is a state bug that no assertion catches."""
    from app.eval.sql.agent.shape_validate import ShapeGateOutcome

    outcome = ShapeGateOutcome(
        messages=(), shape_retries=1, list_query_streak=0, fingerprint_seen=frozenset()
    )
    assert set(outcome.to_state_update()) == {
        "messages",
        "shape_retries",
        "list_query_streak",
        "fingerprint_seen",
    }
