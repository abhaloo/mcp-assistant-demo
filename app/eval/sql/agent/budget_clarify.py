from __future__ import annotations

from langchain_core.messages import AIMessage

from app.eval.sql.agent.clarify import CLARIFY_PREFIX
from app.eval.sql.agent.shape_validate import (
    expected_result_grain,
    first_human_question,
    is_existence_anti_join_list_sql,
    latest_sql_db_query_sql,
    latest_sql_db_query_tool_message,
    parse_sql_tool_result,
)

BUDGET_CLARIFY_STOP_REASON = "budget_clarify"


def build_count_vs_list_clarify_question(
    question: str,
    *,
    grain: str | None = None,
    budget_exhausted: bool = False,
) -> str:
    """Ask count-vs-list when existence / scalar grain conflicts with a multirow list."""
    kind = "count" if grain in {None, "count_scalar"} else "row"
    body = f"For “{question}”, do you want a single {kind}, or a list of matching rows?"
    if budget_exhausted:
        return f"I hit the step limit before finishing. {body}"
    return body


def build_count_vs_list_clarify_message(
    question: str,
    *,
    grain: str | None = None,
    budget_exhausted: bool = False,
) -> AIMessage:
    question_text = build_count_vs_list_clarify_question(
        question, grain=grain, budget_exhausted=budget_exhausted
    )
    return AIMessage(content=f"{CLARIFY_PREFIX} {question_text}")


def build_budget_clarify_question(messages: list) -> str:
    question = first_human_question(messages)
    grain = expected_result_grain(question)
    tool_msg = latest_sql_db_query_tool_message(messages)
    sql = latest_sql_db_query_sql(messages) or ""
    parsed = parse_sql_tool_result(str(tool_msg.content or "")) if tool_msg is not None else None
    multirow = parsed is not None and parsed.outcome == "rows" and parsed.row_count >= 2

    if tool_msg is not None and grain in {"count_scalar", "singular"} and multirow:
        return build_count_vs_list_clarify_question(question, grain=grain, budget_exhausted=True)

    if is_existence_anti_join_list_sql(sql):
        return build_count_vs_list_clarify_question(
            question, grain="count_scalar", budget_exhausted=True
        )

    # date cue
    qlow = (question or "").lower()
    if any(w in qlow for w in ("yesterday", "today", "last week", "last month", "on ")):
        return (
            f"I hit the step limit before finishing. For “{question}”, "
            "should that cover one named day only, or a cumulative/from-date range?"
        )
    return (
        f"I hit the step limit before finishing “{question}”. "
        "Which single metric or filter should I lock first "
        "(for example: exact date range, customer, or ledger vs invoiced)?"
    )


def build_budget_clarify_message(messages: list) -> AIMessage:
    return AIMessage(content=f"{CLARIFY_PREFIX} {build_budget_clarify_question(messages)}")
