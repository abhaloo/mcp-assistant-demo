"""Hand-built LangGraph StateGraph SQL agent (replaces create_sql_agent).

Topology:
  list_tables -> call_get_schema -> get_schema -> generate_query
    -> (tool_calls? no -> END | yes -> run_query) -> generate_query (loop)

The run_query -> generate_query loop IS the self-correction path: the model regenerates
with the tool result/error in context. A separate pre-execution "double-check the SQL"
LLM node was removed (Lever 1) — it fired on every attempt, saw only the SQL string (not
the schema/mapping rules), and the evidence (Huang et al. ICLR 2024; Bosch arXiv
2510.10885) is that a context-blind self-check degrades as often as it helps. Correction
driven by an execution result is the kind that actually works.

The three tools close over one request's role-scoped AnonymizingSQLDatabase, so every
DB round-trip de/anonymizes through the same instance. The execute tool MUST be named
`sql_db_query` (the eval harness CapturingCallback matches on it). All three tools
route through ToolExecution (validate → transform → run → audit); S1 binds RLS on
`sql_db_query` via ``partial(rls_transform_args, row_policy=…)``.
"""

from __future__ import annotations

import re
from datetime import date
from functools import partial
from typing import Any, NotRequired

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from app.config import settings
from app.eval.sql.agent.budget_clarify import (
    build_budget_clarify_message,
)
from app.eval.sql.agent.calendar_windows import format_calendar_windows
from app.eval.sql.agent.clarify import is_clarification_answer
from app.eval.sql.agent.dry_run import explain_validate
from app.eval.sql.agent.metric_lookup import (
    ClarifyRequired,
    MetricHit,
    build_metric_clarify_message,
    format_metric_inject_block,
    metric_resolution_question,
    resolve_metric,
)
from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded, SqlRunBudget
from app.eval.sql.agent.shape_validate import decide_shape_gate
from app.eval.sql.agent.tool_budget import SqlToolOutputBudget
from app.guardrails.sql_guard import is_safe_select, referenced_tables
from app.prompts.rules import _StaticBlock, render_rules
from app.tools.audit import record_tool_execution_audit
from app.tools.execution import execute_tool
from app.tools.transforms import noop_transform, rls_transform_args
from app.tools.types import ToolRejection

_HIGH_PRIORITY_FILTER_RE = re.compile(
    r"(?:\b\w+\.)?[`\"\[]?priority[`\"\]]?\s*=\s*['\"]high['\"]",
    re.IGNORECASE,
)
_DEPARTMENT_12_EXCLUSION_RE = re.compile(
    r"(?:\b\w+\.)?[`\"\[]?department_id[`\"\]]?\s*(?:<>|!=)\s*(?:['\"]?12['\"]?)|"
    r"(?:\b\w+\.)?[`\"\[]?department_id[`\"\]]?\s+NOT\s+IN\s*\(\s*12\s*\)",
    re.IGNORECASE,
)


class SqlAgentState(MessagesState):
    shape_retries: NotRequired[int]
    list_query_streak: NotRequired[int]
    metric_inject: NotRequired[str]
    fingerprint_seen: NotRequired[dict[str, dict[str, Any]]]


def _business_semantics_error(query: str) -> str | None:
    """Reject executable SQL that contradicts a UI-visible business definition."""
    if "work_orders" not in referenced_tables(query):
        return None
    if not _HIGH_PRIORITY_FILTER_RE.search(query):
        return None
    if _DEPARTMENT_12_EXCLUSION_RE.search(query):
        return None
    return (
        "high-priority jobs must match the Jobs UI badge: add "
        "work_orders.department_id <> 12 alongside work_orders.priority = 'high'"
    )


def _apply_statement_timeout(query: str, timeout_ms: int, dialect: str) -> str:
    """MySQL server-side cap via the MAX_EXECUTION_TIME optimizer hint (milliseconds).

    The hint is only honored immediately after the SELECT keyword, so we apply it to
    top-level SELECTs. CTE (WITH …) queries are a KNOWN GAP: a hint placed before WITH
    is parsed as a plain comment and silently ignored, so we do NOT emit one (better an
    un-capped CTE than code that looks applied but isn't). All current pathological cases
    are plain SELECTs; capping CTEs would need a session-level timeout (follow-up)."""
    if timeout_ms <= 0 or dialect.lower() != "mysql":
        return query
    stripped = query.lstrip()
    if stripped.upper().startswith("SELECT"):
        rest = stripped[len("SELECT") :].lstrip()
        return f"SELECT /*+ MAX_EXECUTION_TIME({timeout_ms}) */ {rest}"
    return query


def _is_query_timeout(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "3024" in msg or "max_execution_time" in msg or "maximum statement execution time" in msg


def _validate_sql_query(query: str, *, allowed: set[str]) -> str | ToolRejection:
    """Shared safety predicates for sql_db_query (pre- and post-transform)."""
    ok, reason = is_safe_select(query)
    if not ok:
        return ToolRejection(reason)
    semantics_error = _business_semantics_error(query)
    if semantics_error:
        return ToolRejection(semantics_error)
    out_of_tier = referenced_tables(query) - allowed
    if out_of_tier:
        return ToolRejection(
            f"query references out-of-tier table(s): {', '.join(sorted(out_of_tier))}"
        )
    return query


def build_sql_graph(
    db,
    llm,
    system_content: str,
    *,
    row_policy: Any,
    tool_output_budget: SqlToolOutputBudget | None = None,
    run_budget: SqlRunBudget | None = None,
    principal: Any = None,
    allow_eval_fixture: bool = False,
    today: date | None = None,
    sql_dry_run_explain: bool | None = None,
):
    """Compile the SQL agent graph for one request's role-scoped db.

    db: AnonymizingSQLDatabase (role-scoped via include_tables, anonymizing)
    llm: a tool-calling chat model, e.g. get_chat_model(temperature=0)
    system_content: system prompt with {dialect}/{top_k} already substituted
    row_policy: ScopedSqlPolicy (or successor) bound into sql_db_query transform;
        required at compile — Phase 0 ask denies via ensure_scoped_sql_access before build
    tool_output_budget: row/char caps for sql_db_query; defaults from settings
    run_budget: pre-call LLM invoke caps; one instance per graph build
    principal: optional ask principal threaded for transform/authorize hooks
    allow_eval_fixture: eval/CLI only — required when row_policy is the eval fixture
    today: calendar anchor (same value as dated system prefix); defaults to date.today()
    """
    if row_policy is None:
        raise TypeError(
            "build_sql_graph requires row_policy; Phase 0 ask must deny via "
            "ensure_scoped_sql_access before graph build"
        )
    if getattr(row_policy, "is_eval_cli_fixture", False) and not allow_eval_fixture:
        raise ValueError(
            "eval snapshot fixture requires allow_eval_fixture=True (eval/CLI entrypoints only)"
        )
    if tool_output_budget is None:
        tool_output_budget = SqlToolOutputBudget(
            max_rows=settings.sql_tool_max_rows,
            max_chars=settings.sql_tool_max_chars,
        )
    budget = tool_output_budget
    allowed = set(db.get_usable_table_names())
    timeout_ms = settings.sql_agent_statement_timeout_ms
    dry_run_enabled = (
        settings.sql_dry_run_explain if sql_dry_run_explain is None else sql_dry_run_explain
    )
    query_transform = partial(rls_transform_args, row_policy=row_policy)
    calendar_today = today if today is not None else date.today()

    @tool
    def sql_db_list_tables() -> str:
        """List the tables the current role may query."""

        def validate(args: dict):
            return args

        def run(args: dict) -> str:
            return ", ".join(db.get_usable_table_names())

        return execute_tool(
            name="sql_db_list_tables",
            args={},
            principal=principal,
            validate=validate,
            transform=noop_transform,
            run=run,
            audit=record_tool_execution_audit,
        )

    @tool
    def sql_db_schema(tables: str) -> str:
        """Return (anonymized) schema + sample rows for comma-separated tables."""

        def validate(args: dict):
            requested = [t.strip() for t in str(args.get("tables", "")).split(",") if t.strip()]
            bad = [t for t in requested if t not in allowed]
            if bad:
                return ToolRejection(f"not permitted / unknown table(s): {', '.join(bad)}")
            return {"tables": args.get("tables", ""), "_requested": requested}

        def run(args: dict) -> str:
            return db.get_table_info(args["_requested"])

        return execute_tool(
            name="sql_db_schema",
            args={"tables": tables},
            principal=principal,
            validate=validate,
            transform=noop_transform,
            run=run,
            audit=record_tool_execution_audit,
        )

    @tool
    def sql_db_query(query: str) -> str:
        """Execute a read-only SELECT and return rows (PII re-anonymized by the db)."""

        def validate(args: dict):
            query = str(args.get("query", ""))
            checked = _validate_sql_query(query, allowed=allowed)
            if isinstance(checked, ToolRejection):
                return checked
            if dry_run_enabled:
                dry_run = explain_validate(db, query)
                if isinstance(dry_run, ToolRejection):
                    return dry_run
            return args

        def run(args: dict) -> str | ToolRejection:
            q = str(args.get("query", ""))
            checked = _validate_sql_query(q, allowed=allowed)
            if isinstance(checked, ToolRejection):
                return checked
            timed_query = _apply_statement_timeout(q, timeout_ms, db.dialect)
            params = args.get("_params")
            try:
                return db.run_bounded(timed_query, budget=budget, params=params)
            except Exception as e:  # noqa: BLE001 — execute_tool audits outcome=error
                if _is_query_timeout(e):
                    raise RuntimeError(f"query exceeded {timeout_ms}ms") from e
                raise

        return execute_tool(
            name="sql_db_query",
            args={"query": query},
            principal=principal,
            validate=validate,
            transform=query_transform,
            run=run,
            audit=record_tool_execution_audit,
        )

    @tool
    def sql_calendar_windows() -> str:
        """Return inclusive ISO date windows (today/this_week/…) for relative-date SQL."""

        return format_calendar_windows(calendar_today)

    get_schema_node = ToolNode([sql_db_schema], handle_tool_errors=True)
    run_query_node = ToolNode([sql_db_query, sql_calendar_windows], handle_tool_errors=True)

    def _check_run_budget_or_clarify(state: SqlAgentState):
        if run_budget is None:
            return None
        try:
            run_budget.check_before_call(state["messages"], system_content=system_content)
        except SqlContextBudgetExceeded as exc:
            if exc.reason == "max_llm_calls":
                run_budget.mark_clarify_stop()
                return {"messages": [build_budget_clarify_message(state["messages"])]}
            raise
        return None

    def list_tables(state: SqlAgentState):
        call_id = "seed_list_tables"
        ai = AIMessage(
            content="",
            tool_calls=[
                {"name": "sql_db_list_tables", "args": {}, "id": call_id, "type": "tool_call"}
            ],
        )
        result = sql_db_list_tables.invoke({})
        return {"messages": [ai, ToolMessage(content=result, tool_call_id=call_id)]}

    def call_get_schema(state: SqlAgentState):
        # parallel_tool_calls=False: one tool call per message. Without it gpt-4o-mini
        # can emit a message with hundreds of (often malformed) duplicate tool_calls,
        # which ToolNode then runs one-by-one — recursion_limit caps supersteps, NOT
        # tool_calls per message, so the fan-out produced 200+ DB hits / ~290s runs.
        early = _check_run_budget_or_clarify(state)
        if early is not None:
            return early
        bound = llm.bind_tools([sql_db_schema], tool_choice="any", parallel_tool_calls=False)
        resp = bound.invoke(state["messages"])
        if run_budget is not None:
            run_budget.record_llm_call()
            run_budget.record_response_usage(resp)
        return {"messages": [resp]}

    def route_after_schema_llm(state: SqlAgentState):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and is_clarification_answer(str(last.content or "")):
            return END
        return "get_schema"

    def preinject_metric_definition(state: SqlAgentState):
        messages = list(state["messages"])
        question = metric_resolution_question(messages)
        outcome = resolve_metric(question)
        if isinstance(outcome, ClarifyRequired):
            return {"messages": [build_metric_clarify_message(outcome)]}
        if isinstance(outcome, MetricHit):
            return {"metric_inject": format_metric_inject_block(outcome)}
        return {}

    def route_after_metric_preinject(state: SqlAgentState):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and is_clarification_answer(str(last.content or "")):
            return END
        return "generate_query"

    def generate_query(state: SqlAgentState):
        early = _check_run_budget_or_clarify(state)
        if early is not None:
            return early
        inject = state.get("metric_inject")
        effective_system = render_rules(
            (_StaticBlock("system", system_content), _StaticBlock("metric_inject", inject or "")),
            ctx=None,
        )
        system = {"role": "system", "content": effective_system}
        # Dual-tool route: SQL execute + calendar windows (same ToolNode).
        # parallel_tool_calls=False bounds the loop. Mid-loop schema re-fetch stays out.
        bound = llm.bind_tools([sql_db_query, sql_calendar_windows], parallel_tool_calls=False)
        resp = bound.invoke([system] + list(state["messages"]))
        if run_budget is not None:
            run_budget.record_llm_call()
            run_budget.record_response_usage(resp)
        return {"messages": [resp]}

    def should_continue(state: SqlAgentState):
        last = state["messages"][-1]
        # Malformed/empty tool calls fall through to run_query; the sql_db_query guards
        # (is_safe_select) + ToolNode(handle_tool_errors=True) surface them as a
        # recoverable ToolMessage that the next generate_query can react to.
        return "run_query" if getattr(last, "tool_calls", None) else END

    def route_start(state: SqlAgentState):
        # Shared clarify continuation: after a user reply we already have schema in
        # history — skip list_tables/get_schema bootstrap (Contract shared-clarify).
        n_human = sum(1 for m in state["messages"] if isinstance(m, HumanMessage))
        return "preinject_metric_definition" if n_human > 1 else "list_tables"

    def validate_result_shape(state: SqlAgentState) -> dict[str, Any]:
        return decide_shape_gate(state).to_state_update()

    def route_after_shape(state: SqlAgentState):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and is_clarification_answer(str(last.content or "")):
            return END
        return "preinject_metric_definition"

    g = StateGraph(SqlAgentState)
    g.add_node("list_tables", list_tables)
    g.add_node("call_get_schema", call_get_schema)
    g.add_node("get_schema", get_schema_node)
    g.add_node("preinject_metric_definition", preinject_metric_definition)
    g.add_node("generate_query", generate_query)
    g.add_node("run_query", run_query_node)
    g.add_node("validate_result_shape", validate_result_shape)

    g.add_conditional_edges(
        START,
        route_start,
        {
            "list_tables": "list_tables",
            "preinject_metric_definition": "preinject_metric_definition",
        },
    )
    g.add_edge("list_tables", "call_get_schema")
    g.add_conditional_edges(
        "call_get_schema",
        route_after_schema_llm,
        ["get_schema", END],
    )
    g.add_edge("get_schema", "preinject_metric_definition")
    g.add_conditional_edges(
        "preinject_metric_definition",
        route_after_metric_preinject,
        ["generate_query", END],
    )
    g.add_conditional_edges("generate_query", should_continue, ["run_query", END])
    g.add_edge("run_query", "validate_result_shape")
    g.add_conditional_edges(
        "validate_result_shape",
        route_after_shape,
        ["preinject_metric_definition", END],
    )
    return g.compile()
