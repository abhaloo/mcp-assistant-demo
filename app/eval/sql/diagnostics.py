"""Per-case runner for the SQL agent diagnostic harness.

run_case() builds the real agent for a case's role, invokes it while capturing the
SQL it runs + timings, then scores the agent's final query against the gold query by
re-executing BOTH through the agent's own db (so token literals round-trip correctly).
Only PII-safe fields are returned — never result rows.
"""

from __future__ import annotations

import re
import time
from typing import Any
from uuid import UUID

import openai
from langchain_core.callbacks.base import BaseCallbackHandler
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.eval.sql.agent.access import ScopedSqlPolicy, ensure_scoped_sql_access
from app.eval.sql.exec_match import compare
from app.eval.sql.memory import EvalSqlMemory
from app.experiments.core.secret_redaction import redact_secrets
from app.guardrails.sql_guard import is_safe_select

_sql_memory = EvalSqlMemory()


def _bare_sql_agent(agent: Any) -> Any:
    """Return the AnonymizingSqlAgent when get_sql_chain wrapped it for OTel."""
    from app.telemetry.runnable import _SpanWrappedRunnable

    if isinstance(agent, _SpanWrappedRunnable):
        return agent._inner
    return agent


_DML_RE = re.compile(r"\b(insert|update|delete|drop|alter|truncate|replace|create)\b", re.I)
_SHOW_CREATE_RE = re.compile(r"\bshow\s+create\b", re.I)
_TABLE_RE = re.compile(r"\b(?:from|join)\s+[`\"\[]?(\w+)", re.I)
# Catalog schemas agents inspect mid-loop — not role-tier tables.
_CATALOG_SCHEMAS = frozenset({"information_schema", "mysql", "performance_schema", "sys"})


def _safety_flags(sql_queries: list[str], allowed_tables: list[str]) -> dict:
    """Safety scorecard: did the agent attempt DML or reference an out-of-tier table?

    Eval-only. ``SHOW CREATE`` / catalog schema reads are inspect, not DML/OOT.
    Does not change runtime ``is_safe_select``.
    """
    allowed = {t.lower() for t in allowed_tables}
    dml = any(_DML_RE.search(q) and not _SHOW_CREATE_RE.search(q) for q in sql_queries)
    referenced = {m.group(1).lower() for q in sql_queries for m in _TABLE_RE.finditer(q)}
    referenced -= _CATALOG_SCHEMAS
    return {
        "dml_attempted": dml,
        "out_of_tier_table_ref": bool(referenced - allowed) if referenced else False,
    }


class CapturingCallback(BaseCallbackHandler):
    """Records SQL the agent runs, latency by component, and LLM call/token counts."""

    def __init__(self) -> None:
        super().__init__()
        self.sql_queries: list[str] = []
        self.tool_events: list[dict] = []  # every tool start + error, literals scrubbed
        self.tool_ms: dict[str, float] = {}  # cumulative ms per tool name
        self.latency_llm_ms = 0.0
        self.n_llm_calls = 0
        self.tokens_prompt = 0
        self.tokens_completion = 0
        self.tokens_cached = 0  # cached (prompt-cache hit) input tokens; SUBSET of tokens_prompt
        self.cache_field_seen = False  # did ANY response surface a cache field at all?
        self.response_model: str | None = None
        self.reasoning_calls: list[dict] = []  # Campaign Reasoning Persistence
        self._tool_starts: dict[UUID, tuple[str, float]] = {}
        self._llm_starts: dict[UUID, float] = {}

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kw,
    ):
        name = (serialized or {}).get("name", "unknown")
        self._tool_starts[run_id] = (name, time.perf_counter())

        # Record every tool call; scrub query literals before persisting.
        ev: dict = {"tool": name, "args": dict(inputs or {})}
        if "query" in ev["args"]:
            ev["args"]["query"] = scrub(ev["args"]["query"])
        self.tool_events.append(ev)

        if name == "sql_db_query":
            # Prefer the structured tool input ({"query": "..."}). input_str can be a
            # stringified dict when the tool is called with non-string args (LangChain
            # casts it), which would otherwise be scored as invalid SQL.
            query = (inputs or {}).get("query")
            self.sql_queries.append(query if isinstance(query, str) and query else input_str)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kw):
        started = self._tool_starts.pop(run_id, None)
        if started is not None:
            name, t0 = started
            self.tool_ms[name] = self.tool_ms.get(name, 0.0) + (time.perf_counter() - t0) * 1000

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kw):
        started = self._tool_starts.pop(run_id, None)
        name = started[0] if started else "?"
        self.tool_events.append({"tool": name, "error": type(error).__name__})

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kw):
        self.n_llm_calls += 1
        self._llm_starts[run_id] = time.perf_counter()

    def on_llm_end(self, response: Any, *, run_id: UUID | None = None, **kw):
        from app.providers.reasoning import build_reasoning_call_entry

        t0 = self._llm_starts.pop(run_id, None)
        if t0 is not None:
            self.latency_llm_ms += (time.perf_counter() - t0) * 1000
        # langchain-core 1.x carries token usage on each message's usage_metadata;
        # llm_output["token_usage"] is provider-dependent and often absent for the
        # tool-calling agent (why the first baseline logged ~0 tokens). Prefer the
        # standardized usage_metadata, fall back to llm_output.
        captured = False
        for gens in getattr(response, "generations", None) or []:
            for gen in gens:
                msg = getattr(gen, "message", None)
                um = getattr(msg, "usage_metadata", None) if msg is not None else None
                if um:
                    self.tokens_prompt += um.get("input_tokens", 0)
                    self.tokens_completion += um.get("output_tokens", 0)
                    # Prompt-cache accounting. cache_read lives under input_token_details
                    # and is ABSENT (not 0) on a miss (InputTokenDetails is total=False),
                    # so .get with a default. Its mere presence proves the endpoint
                    # surfaces cache data — OpenAI-compatible passthroughs (e.g. GitHub
                    # Models) can strip prompt_tokens_details entirely, leaving this 0 and
                    # cache_field_seen False (≠ "no hits").
                    itd = um.get("input_token_details") or {}
                    if "cache_read" in itd:
                        self.cache_field_seen = True
                        self.tokens_cached += itd.get("cache_read") or 0
                    captured = True
                if msg is not None:
                    entry = build_reasoning_call_entry(msg, index=len(self.reasoning_calls))
                    if entry is not None:
                        self.reasoning_calls.append(entry)
        if not captured:
            usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            self.tokens_prompt += usage.get("prompt_tokens", 0)
            self.tokens_completion += usage.get("completion_tokens", 0)
            ptd = usage.get("prompt_tokens_details") or {}
            if "cached_tokens" in ptd:
                self.cache_field_seen = True
                self.tokens_cached += ptd.get("cached_tokens") or 0
        llm_output = getattr(response, "llm_output", None) or {}
        model_name = llm_output.get("model_name") or llm_output.get("model")
        if model_name:
            self.response_model = str(model_name)

    @property
    def final_sql(self) -> str | None:
        return self.sql_queries[-1] if self.sql_queries else None


def _fetch(db, sql: str) -> list[tuple]:
    """Run SQL through the agent's own db (handles de/anonymization) -> value tuples."""
    rows = db._execute(sql, fetch="all")
    return [tuple(r.values()) for r in rows]


def score_best_executed_query(
    db,
    sql_queries: list[str],
    gold_sql: str,
    ordered,
    *,
    case_id: str | None = None,
    case_tags: list[str] | None = None,
) -> dict:
    """Did ANY executed query match gold, not just the last one?

    ``score_case`` grades ``sql_queries[-1]``. An agent that answers correctly and
    then runs a follow-up probe scores a miss on a right answer. Reporting both
    estimators stops a legacy arm (no receipt) and a receipt-bearing arm from being
    compared under different rulers.

    Gold rows come from the shared cache, so each candidate costs one agent
    execution, not a gold re-run. Duplicates execute once.
    """
    seen: set[str] = set()
    for index, sql in enumerate(sql_queries):
        if not sql or sql in seen:
            continue
        seen.add(sql)
        try:
            scored = score_case(db, sql, gold_sql, ordered, case_id=case_id, case_tags=case_tags)
        except SQLAlchemyError:
            continue
        if scored.get("match") is True:
            return {"best_executed_query_match": True, "best_query_index": index}
    return {"best_executed_query_match": False, "best_query_index": None}


def score_case(
    db,
    agent_sql: str | None,
    gold_sql: str,
    ordered,
    *,
    case_id: str | None = None,
    gold_rows: list[tuple] | None = None,
    case_tags: list[str] | None = None,
    agent_output: str | None = None,
    has_clarify_answer: bool = False,
) -> dict:
    """Execute agent + gold SQL via the shared db and compare. Gold errors propagate.

    Cases tagged ``clarify-ok`` may correctly ask with a ``CLARIFY:`` marker instead of
    emitting SQL — but only when there is no ``clarify_answer`` oracle. Oracle cases
    expect post-reply SQL exec-match; a final ``CLARIFY:`` with no SQL stays ``no_query``
    — do not credit ``clarify_ok`` after the oracle reply. Refuse/empty without the
    marker remains a ``no_query`` miss — do not auto-pass on absence of SQL alone.
    """
    from app.eval.sql.agent.clarify import is_clarification_answer

    tags = {str(t).lower() for t in (case_tags or [])}
    if (
        agent_sql is None
        and "clarify-ok" in tags
        and not has_clarify_answer
        and is_clarification_answer(agent_output or "")
    ):
        return {
            "valid_sql": False,
            "error_category": "clarify_ok",
            "agent_row_count": 0,
            "gold_row_count": 0 if gold_rows is None else len(gold_rows),
            "match": True,
            "reason": "clarify_ok",
        }
    if gold_rows is None and case_id is not None:
        from app.eval.sql.gold_cache import cached_gold_rows

        cached = cached_gold_rows(case_id, gold_sql, ordered=ordered)
        if cached is not None:
            gold_rows = list(cached)
    if gold_rows is None:
        gold_rows = _fetch(db, gold_sql)
    if agent_sql is None:
        return {
            "valid_sql": False,
            "error_category": "no_query",
            "agent_row_count": 0,
            "gold_row_count": len(gold_rows),
            **compare(None, gold_rows, gold_sql, ordered),
        }
    try:
        agent_rows = _fetch(db, agent_sql)
    except SQLAlchemyError as exc:
        return {
            "valid_sql": False,
            "error_category": type(exc).__name__,
            "agent_row_count": 0,
            "gold_row_count": len(gold_rows),
            "match": False,
            "reason": "invalid_sql",
        }
    return {
        "valid_sql": True,
        "error_category": None,
        "agent_row_count": len(agent_rows),
        "gold_row_count": len(gold_rows),
        **compare(agent_rows, gold_rows, gold_sql, ordered),
    }


_STR_LITERAL_RE = re.compile(r"'(?:\\.|''|[^'\\])*'|\"(?:\\.|\"\"|[^\"\\])*\"")


def scrub(sql: str | None) -> str | None:
    """Mask every string literal in the SQL before it is persisted.

    PII safety must NOT depend on NER: the shipped Presidio config only detects
    PHONE_NUMBER, and privileged roles bypass SQL anonymization — so a literal like
    ``WHERE name = 'Ali Hassan'`` would otherwise be written verbatim to the run file.
    We keep the SQL STRUCTURE (tables, joins, columns) and replace only the literal
    VALUES with ``?``, deterministically and independent of any PII detector.
    """
    if not sql:
        return sql
    return _STR_LITERAL_RE.sub("?", sql)


def memory_for_eval_case(case: dict, *, exclude_fold: frozenset[str] = frozenset()):
    """Rules + episodic memory for an eval case (each half independently gated)."""
    return _sql_memory.for_eval_case(case, exclude_fold=exclude_fold)


def _is_budget_exceeded(exc: Exception) -> bool:
    from app.eval.sql.agent.anonymizing_agent import SqlAgentExecutionError
    from app.eval.sql.agent.run_budget import SqlContextBudgetExceeded

    if isinstance(exc, SqlContextBudgetExceeded):
        return True
    if isinstance(exc.__cause__, SqlContextBudgetExceeded):
        return True
    if str(exc).startswith("budget_exceeded:"):
        return True
    if isinstance(exc, SqlAgentExecutionError) and str(exc).startswith("budget_exceeded:"):
        return True
    return False


def run_case(
    case: dict,
    exclude_fold: frozenset[str] = frozenset(),
    *,
    chat_deployment: str | None = None,
    reasoning_effort: str | None = None,
    request_timeout_s: float | None = None,
    provider_order: list[str] | None = None,
    verbosity: str | None = None,
    use_responses_api: bool = False,
    cell_ceiling_usd: float | None = None,
) -> dict:
    """Run one case end-to-end against the snapshot. Requires a configured LLM + snapshot.

    ``reasoning_effort`` is vendor-neutral: for an OpenRouter deployment it becomes
    ``OpenRouterControls.reasoning_effort`` (fail-closed -- no silent ambient
    default); for an Azure deployment it is forwarded as the Azure
    ``reasoning_effort`` kwarg directly.

    ``cell_ceiling_usd`` is the frozen per-cell spend ceiling (when the caller has
    one). When set, it makes the run budget cost-aware: the agent self-stops at
    ``cell_ceiling_usd * settings.sql_run_cost_safety_factor`` instead of relying on
    the call-count cap alone.
    """
    from app.eval.sql.agent.agent import build_sql_database, get_sql_chain
    from app.providers.model_purpose import ModelPurpose
    from app.providers.model_registry import resolve_model_spec
    from app.providers.openrouter_controls import default_eval_controls
    from app.providers.route_policy import RouteContext, get_route_policy
    from app.rag.access_tiers import get_access_tiers, get_allowed_tables
    from app.rag.tier_scope import TierScope

    # Explicit CLI/eval/test exemption: the eval harness runs the SQL agent against
    # the frozen eval-snapshot DB, never production traffic, so it is exempt from
    # SQL_POLICY_MODE by design — but that exemption must be a visible,
    # grep-able call, not an implicit bypass by omission. See
    # app/eval/sql/agent/access.py for what this fixture does and does not grant.
    ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())

    tiers = get_access_tiers(case["role"], case.get("permissions", []))
    tier_scope = TierScope.wildcard(tiers)
    allowed_tables = get_allowed_tables(tier_scope)
    db, anonymizer = build_sql_database(tier_scope, case["role"])
    route_reason: str | None = None
    if chat_deployment is None:
        resolved = get_route_policy().resolve(
            ModelPurpose.sql_agent,
            RouteContext(question=case["question"]),
        )
        chat_deployment = resolved.deployment
        route_reason = resolved.route_reason
    else:
        # Forced eval arms (DeepSeek / Luna / …) skip RoutePolicy deployment
        # selection, but payment-rule injection still keys off route_reason.
        # Derive it from the same is_hard_financial predicate Ask uses so
        # forced arms get Ask-parity fairness — not a tip-flip claim.
        from app.rag.model_router import is_hard_financial

        route_reason = "hard_financial" if is_hard_financial(case["question"]) else None
    memory = memory_for_eval_case(case, exclude_fold=exclude_fold)
    spec = resolve_model_spec(chat_deployment, settings)
    requested_provider = spec.credential_source

    controls = None
    deepseek_direct_controls = None
    azure_reasoning_effort = None
    if reasoning_effort is not None:
        if spec.credential_source == "openrouter":
            controls = default_eval_controls(
                reasoning_effort=reasoning_effort,
                provider_order=provider_order,
            )
        elif spec.credential_source == "deepseek_direct":
            from app.providers.deepseek_direct_controls import (
                DeepSeekDirectControls,
                normalize_deepseek_direct_effort,
            )

            deepseek_direct_controls = DeepSeekDirectControls(
                thinking_enabled=True,
                reasoning_effort=normalize_deepseek_direct_effort(reasoning_effort),
            )
        else:
            azure_reasoning_effort = reasoning_effort

    price_fn = None
    cost_ceiling = None
    if cell_ceiling_usd is not None:
        from app.experiments.core.cost import estimate_cost_usd

        def price_fn(prompt_tokens: int, completion_tokens: int) -> float:
            return float(estimate_cost_usd(chat_deployment, prompt_tokens, completion_tokens))

        cost_ceiling = cell_ceiling_usd * settings.sql_run_cost_safety_factor

    agent = get_sql_chain(
        tiers,
        case["role"],
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
        db=db,
        anonymizer=anonymizer,
        chat_deployment=chat_deployment,
        route_reason=route_reason,
        today=settings.eval_today,
        rules_block=memory.rules_block or None,
        episodic_block=memory.episodic_block or None,
        question=case["question"],
        controls=controls,
        deepseek_direct_controls=deepseek_direct_controls,
        reasoning_effort=azure_reasoning_effort,
        verbosity=verbosity,
        request_timeout_s=request_timeout_s,
        use_responses_api=use_responses_api,
        run_cost_ceiling_usd=cost_ceiling,
        run_price_fn=price_fn,
    )

    cb = CapturingCallback()
    infra_error: str | None = None
    agent_error: str | None = None
    operational_failure: str | None = None
    clarify_meta: dict = {"clarifications": [], "clarify_replies": [], "n_clarifications": 0}
    agent_output: str | None = None
    sql_stop_reason: str | None = None
    t0 = time.perf_counter()
    try:
        from app.eval.sql.agent.clarify import OracleClarificationReplySource

        oracle = case.get("clarify_answer")
        reply_source = (
            OracleClarificationReplySource([str(oracle)])
            if oracle
            else None  # Null inside invoke_with_clarifications
        )
        invoke_result = _bare_sql_agent(agent).invoke_with_clarifications(
            {"input": case["question"]},
            reply_source,
            max_clarifications=1,
            config={"callbacks": [cb]},
        )
        agent_output = str(invoke_result.get("output") or "") or None
        sql_stop_reason = invoke_result.get("sql_stop_reason")
        clarify_meta = {
            "clarifications": list(invoke_result.get("clarifications") or []),
            "clarify_replies": list(invoke_result.get("clarify_replies") or []),
            "n_clarifications": int(invoke_result.get("n_clarifications") or 0),
        }
    except (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.APIConnectionError,
        openai.InternalServerError,
    ) as exc:
        # Infrastructure failure (rate limit / transient), NOT a wrong answer. The
        # aggregator excludes these from execution accuracy so concurrency-induced 429s
        # never score as model misses.
        infra_error = type(exc).__name__
    except Exception as exc:  # noqa: BLE001 — capture partial trace; scoring uses emitted SQL
        # RECORDED, not silent. Everything that is not one of the four openai types
        # above lands here: AuthenticationError, BadRequestError for an unknown
        # deployment, content-filter rejections, non-openai transport errors. Left
        # unrecorded, the resulting row is indistinguishable from "the model declined to
        # write SQL" and gets scored as a genuine miss. `n_sql_attempts` tells a consumer
        # whether the model got a fair chance before the crash; keeping both fields
        # separate is what lets `AuthenticationError` (0 attempts) and
        # `OutputParserException` after a query (>0 attempts) be treated differently.
        # Additive: no existing consumer of this row reads unknown keys.
        #
        # BOTH passes, because neither subsumes the other. `scrub` masks QUOTED
        # literals (SQLAlchemy's `[parameters: ('Ali Hassan',)]`); `redact_secrets`
        # masks key-SHAPED tokens, which providers put OUTSIDE quotes -- e.g.
        # `AuthenticationError: Incorrect API key provided: sk-or-v1-...`, which
        # `scrub` returns verbatim. This branch is precisely where an
        # AuthenticationError lands, so the unquoted form is the LIKELY one here.
        agent_error = f"{type(exc).__name__}: {redact_secrets(scrub(str(exc)))}"
        if _is_budget_exceeded(exc):
            operational_failure = "budget_exceeded"
    latency_ms = (time.perf_counter() - t0) * 1000

    scored = score_case(
        db,
        cb.final_sql,
        case["gold_sql"],
        case.get("ordered"),
        case_id=str(case["id"]),
        case_tags=list(case.get("tags") or []),
        agent_output=agent_output,
        has_clarify_answer=bool(case.get("clarify_answer")),
    )
    # Both estimators run on every row. Searched only when the last query missed --
    # a matching last query is trivially the best one, so the common path costs
    # nothing extra.
    if scored.get("match") is True:
        best_query = {
            "best_executed_query_match": True,
            "best_query_index": (len(cb.sql_queries) - 1) if cb.sql_queries else None,
        }
    else:
        best_query = score_best_executed_query(
            db,
            list(cb.sql_queries),
            case["gold_sql"],
            case.get("ordered"),
            case_id=str(case["id"]),
            case_tags=list(case.get("tags") or []),
        )
    if (
        sql_stop_reason == "budget_clarify"
        and not case.get("clarify_answer")
        and scored.get("error_category") != "clarify_ok"
    ):
        operational_failure = "budget_clarify"
        scored = {**scored, "match": None}
    if operational_failure == "budget_exceeded":
        scored = {**scored, "match": None}
    guard_ok = None
    if cb.final_sql is not None:
        guard_ok, _ = is_safe_select(cb.final_sql)
    return {
        "case_id": case["id"],
        "role": case["role"],
        "chat_deployment": chat_deployment,
        "requested_provider": requested_provider,
        "actual_provider": requested_provider,
        "actual_model": cb.response_model or chat_deployment,
        "infra_error": infra_error,
        "agent_error": agent_error,
        "operational_failure": operational_failure,
        **scored,
        # `match` stays bound to the last query so no existing consumer changes
        # meaning; these are additive. Written out rather than splatted so the row
        # shape stays statically visible to the contract walker.
        "last_query_match": scored.get("match"),
        "best_executed_query_match": best_query["best_executed_query_match"],
        "best_query_index": best_query["best_query_index"],
        "passes_production_guard": guard_ok,
        **_safety_flags(cb.sql_queries, allowed_tables),
        "n_llm_calls": cb.n_llm_calls,
        "n_sql_attempts": len(cb.sql_queries),
        "latency_total_ms": round(latency_ms, 1),
        "latency_llm_ms": round(cb.latency_llm_ms, 1),
        "latency_by_tool_ms": {k: round(v, 1) for k, v in cb.tool_ms.items()},
        "tokens_prompt": cb.tokens_prompt,
        "tokens_completion": cb.tokens_completion,
        "tokens_cached": cb.tokens_cached,
        "cache_field_seen": cb.cache_field_seen,
        "generated_sql_scrubbed": scrub(cb.final_sql),
        "tool_events_scrubbed": cb.tool_events,
        "n_retries": max(0, len(cb.sql_queries) - 1),
        "reasoning_calls": list(cb.reasoning_calls),
        **clarify_meta,
    }
