"""Classify SQL agent execution errors into incomplete vs no_answer."""

from app.eval.sql.agent.anonymizing_agent import SqlAgentExecutionError
from app.eval.sql.agent.run_outcome import (
    INCOMPLETE_ANSWER_MESSAGE,
    classify_sql_agent_execution_error,
)


def test_budget_exceeded_is_incomplete():
    stop = classify_sql_agent_execution_error(
        SqlAgentExecutionError("budget_exceeded:max_llm_calls")
    )
    assert stop.kind == "incomplete"
    assert stop.reason == "max_llm_calls"
    assert stop.user_message == INCOMPLETE_ANSWER_MESSAGE


def test_cumulative_cost_is_incomplete():
    stop = classify_sql_agent_execution_error(
        SqlAgentExecutionError("budget_exceeded:cumulative_cost_usd")
    )
    assert stop.kind == "incomplete"
    assert stop.reason == "cumulative_cost_usd"


def test_generic_no_answer_is_not_incomplete():
    stop = classify_sql_agent_execution_error(
        SqlAgentExecutionError("graph produced no final answer")
    )
    assert stop.kind == "no_answer"


def test_stall_via_budget_wrapper_is_incomplete():
    stop = classify_sql_agent_execution_error(
        SqlAgentExecutionError("budget_exceeded:stall_no_progress")
    )
    assert stop.kind == "incomplete"
    assert stop.reason == "stall_no_progress"


def test_stall_prefix_is_incomplete():
    stop = classify_sql_agent_execution_error(SqlAgentExecutionError("stall_no_progress"))
    assert stop.kind == "incomplete"
    assert stop.reason == "stall_no_progress"


def test_substring_stall_in_unrelated_message_is_not_incomplete():
    stop = classify_sql_agent_execution_error(
        SqlAgentExecutionError("graph stall produced no final answer")
    )
    assert stop.kind == "no_answer"
