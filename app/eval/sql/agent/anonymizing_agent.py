"""Agent decorator with reversible PII tokenization for the legacy SQL agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

if TYPE_CHECKING:
    from app.eval.sql.agent.anonymizer import SqlAnonymizer
    from app.eval.sql.agent.run_budget import SqlRunBudget

FALLBACK_ANSWER = "I couldn't produce an answer for that question. Please try rephrasing it."


class SqlAgentNoAnswer(Exception):
    """The graph terminated without a final natural-language answer."""


class SqlAgentExecutionError(Exception):
    """The SQL agent graph failed (recursion limit or no final answer)."""


def _final_answer(messages: list) -> str:
    """Last AIMessage with no tool_calls and real content = the final answer.
    Reverse-scan: the tail may be a ToolMessage or an empty tool-calling AIMessage."""
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls and (m.content or "").strip():
            return str(m.content)
    raise SqlAgentNoAnswer("graph produced no final answer")


def walk_query_provenance(messages: list) -> tuple[list[str], str]:
    """Collect successful sql_db_query SQL strings and all successful rows text.

    This stays in message-state form: tokenized queries + tokenized rows. Display
    de-anonymization happens at the API boundary, not inside this walk.
    """
    tool_results: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, ToolMessage):
            tool_results[msg.tool_call_id] = str(msg.content or "")

    queries: list[str] = []
    rows: list[str] = []
    for msg in messages:
        if not isinstance(msg, AIMessage) or not msg.tool_calls:
            continue
        for call in msg.tool_calls:
            if call.get("name") != "sql_db_query":
                continue
            args = call.get("args") or {}
            sql = args.get("query") or args.get("sql") or ""
            if not sql:
                continue
            result_text = tool_results.get(call["id"], "")
            if result_text.strip().lower().startswith("error"):
                continue
            queries.append(sql)
            rows.append(result_text)
    return queries, "\n".join(rows)


_walk_query_provenance = walk_query_provenance


class AnonymizingSqlAgent:
    """
    Decorator over the compiled LangGraph SQL agent.

    Tokenizes the user's question before it enters the graph and deanonymizes the
    final answer before returning. Tool-output anonymization is handled by
    AnonymizingSQLDatabase (the graph's tools call the same db). Presents the legacy
    {"input"} -> {"output"} contract so ask_service and the eval harness are unchanged.
    """

    def __init__(
        self,
        agent,
        anonymizer: SqlAnonymizer,
        *,
        run_budget: SqlRunBudget | None = None,
    ):
        self._agent = agent  # a CompiledStateGraph
        self._anonymizer = anonymizer
        self.run_budget = run_budget

    @property
    def _run_budget(self) -> SqlRunBudget | None:
        return self.run_budget

    @_run_budget.setter
    def _run_budget(self, value: SqlRunBudget | None) -> None:
        self.run_budget = value

    def invoke(self, inputs: dict, config=None, *, prior_messages=None):
        question = inputs.get("input", "")
        if self.run_budget is not None:
            self.run_budget.reset()
        clean = self._anonymizer.anonymize(question, source="sql_question")
        if prior_messages is None:
            self._anonymizer.clear_execution_records()
            messages = [HumanMessage(clean)]
        else:
            messages = list(prior_messages) + [HumanMessage(clean)]
        try:
            result = self._agent.invoke({"messages": messages}, config)
            answer = _final_answer(result["messages"])
        except (GraphRecursionError, SqlAgentNoAnswer) as exc:
            raise SqlAgentExecutionError(str(exc)) from exc
        except Exception as exc:
            from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded

            if isinstance(exc, SqlContextBudgetExceeded):
                raise SqlAgentExecutionError(f"budget_exceeded:{exc.reason}") from exc
            raise
        queries, last_rows = walk_query_provenance(result["messages"])
        deanonymized_answer = self._anonymizer.deanonymize(answer)
        deanonymized_queries = [self._anonymizer.deanonymize(q) for q in queries]
        sql_stop_reason = None
        if self.run_budget is not None and self.run_budget.consume_clarify_stop():
            from app.eval.sql.agent.budget_clarify import BUDGET_CLARIFY_STOP_REASON

            sql_stop_reason = BUDGET_CLARIFY_STOP_REASON
        return {
            "output": deanonymized_answer,
            "queries": deanonymized_queries,
            "messages": result["messages"],
            "raw": {"answer": answer, "queries": queries, "rows": last_rows},
            "deanonymize": self._anonymizer.deanonymize,
            "execution_records": list(self._anonymizer.execution_records),
            "sql_stop_reason": sql_stop_reason,
        }

    def invoke_with_clarifications(
        self,
        inputs: dict,
        reply_source=None,
        *,
        max_clarifications: int = 1,
        config=None,
    ):
        """Shared prod/eval entry: clarify loop with a swappable reply source.

        Prod default: ``NullClarificationReplySource`` (same path; no auto-answer until UI).
        Eval: ``OracleClarificationReplySource`` from ``case["clarify_answer"]``.
        """
        from app.eval.sql.agent.clarify import (
            NullClarificationReplySource,
            run_with_clarifications,
        )

        source = reply_source if reply_source is not None else NullClarificationReplySource()

        def turn(*, input: str | None = None, prior_messages=None, config=None):
            return self.invoke({"input": input or ""}, config, prior_messages=prior_messages)

        run = run_with_clarifications(
            turn,
            inputs.get("input", ""),
            source,
            max_clarifications=max_clarifications,
            config=config,
        )
        return {
            "output": run.output,
            "queries": run.queries,
            "raw": run.raw,
            "deanonymize": run.deanonymize or self._anonymizer.deanonymize,
            "execution_records": run.execution_records,
            "clarifications": run.clarifications,
            "clarify_replies": run.replies,
            "n_clarifications": run.n_clarifications,
            "sql_stop_reason": run.sql_stop_reason,
        }

    async def astream_status(
        self, inputs: dict, config=None, *, clean: str | None = None
    ) -> AsyncIterator[tuple[str, str | dict | list[str]]]:
        """Stream node-transition statuses, then the de-anonymized final answer.

        Mirrors invoke(): tokenize the question, run the graph, extract the last
        content-bearing AIMessage, de-anonymize it. Yields ("status", node_name)
        per node update and a single terminal ("answer", text). Raises
        SqlAgentExecutionError on recursion / no-answer (same taxonomy as invoke).

        `clean` is a pre-anonymized question. anonymize() runs Presidio NER under a
        threading.Lock (CPU + a blocking lock), so the SSE caller anonymizes off the
        event loop (in _build_sql_stream_agent's worker thread) and passes the result
        here. When None we anonymize inline — used by unit tests with fake graphs.
        """
        from app.eval.sql.agent.anonymizing_database import _tables_from_generate_query

        if clean is None:
            clean = self._anonymizer.anonymize(inputs.get("input", ""), source="sql_question")
        if self.run_budget is not None:
            self.run_budget.reset()
        self._anonymizer.clear_execution_records()
        final_state: dict | None = None
        try:
            async for mode, chunk in self._agent.astream(
                {"messages": [HumanMessage(clean)]},
                config,
                stream_mode=["updates", "values"],
            ):
                if mode == "updates":
                    for node_name, update in chunk.items():
                        yield ("status", node_name)
                        if node_name == "generate_query":
                            tables = _tables_from_generate_query(update)
                            if tables:
                                yield ("tables", tables)
                elif mode == "values":
                    final_state = chunk
            if final_state is None:
                raise SqlAgentNoAnswer("graph produced no state")
            answer = _final_answer(final_state["messages"])
        except (GraphRecursionError, SqlAgentNoAnswer) as exc:
            raise SqlAgentExecutionError(str(exc)) from exc
        except Exception as exc:
            from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded

            if isinstance(exc, SqlContextBudgetExceeded):
                raise SqlAgentExecutionError(f"budget_exceeded:{exc.reason}") from exc
            raise
        deanonymized_answer = self._anonymizer.deanonymize(answer)
        queries, last_rows = walk_query_provenance(final_state["messages"])
        sql_stop_reason = None
        if self.run_budget is not None and self.run_budget.consume_clarify_stop():
            from app.eval.sql.agent.budget_clarify import BUDGET_CLARIFY_STOP_REASON

            sql_stop_reason = BUDGET_CLARIFY_STOP_REASON
        yield ("answer", deanonymized_answer)
        yield (
            "provenance",
            {
                "queries": [self._anonymizer.deanonymize(q) for q in queries],
                "raw": {"answer": answer, "queries": queries, "rows": last_rows},
                "execution_records": list(self._anonymizer.execution_records),
                "sql_stop_reason": sql_stop_reason,
            },
        )
