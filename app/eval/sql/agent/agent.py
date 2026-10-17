"""
SQL agent chain: natural language → SQL query → natural language answer.

This is Flow 2 of Phase 2. The SQL agent handles "structured" questions —
things like "How many orders this week?" or "What's pending approval?" —
where the answer lives in the billing database, not in documents.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

from sqlalchemy.engine import Engine

from app.config import settings
from app.db.reflection import get_reflected_metadata
from app.eval.sql.agent import billing_engine as _billing_engine
from app.eval.sql.agent.anonymizer import SqlAnonymizer
from app.eval.sql.agent.anonymizing_agent import AnonymizingSqlAgent
from app.eval.sql.agent.anonymizing_database import AnonymizingSQLDatabase
from app.eval.sql.agent.graph import build_sql_graph
from app.eval.sql.agent.rules import SQL_PROMPT_RULES, SqlPromptContext
from app.eval.sql.agent.run_budget import SqlRunBudget
from app.eval.sql.agent.tool_budget import SqlToolOutputBudget
from app.prompts.registry import registry
from app.prompts.rules import render_rules
from app.providers import get_chat_model
from app.providers.deepseek_direct_controls import DeepSeekDirectControls
from app.providers.model_purpose import ModelPurpose
from app.providers.model_registry import PolicyViolationError, resolve_model_spec
from app.providers.openrouter_controls import OpenRouterControls
from app.providers.route_policy import RouteContext
from app.rag.access_tiers import get_allowed_tables
from app.rag.tier_scope import TierScope
from app.telemetry.context import current_query_id
from app.telemetry.helpers import record_sql_anonymization
from app.telemetry.runnable import wrap_with_span
from app.telemetry.spans import guardrails_sql_anonymize_span

# Module-level engine binding, deliberately a plain attribute (not a
# getter-only function): app/eval/sql_snapshot.py::point_agent_at_snapshot
# and ~20 tests (tests/rag/chains/test_sql_chain.py,
# tests/rag/test_sql_budget_clarify_continuation.py) repoint the SQL chain at
# a different engine (a frozen eval snapshot, or a sqlite fixture) by
# monkeypatching this exact name -- `sql_chain.engine = <other engine>`.
# Starts unbound (None) so importing this module never opens the production
# MySQL pool; `_resolve_engine()` below
# lazily binds it to the shared billing engine on first real use.
engine: Engine | None = None


def _resolve_engine() -> Engine:
    """Return `engine`, lazily binding it to the shared billing engine on
    first use. If the eval harness (or a test) has already set `engine`
    directly, that binding wins and the shared engine is never touched."""
    global engine
    if engine is None:
        engine = _billing_engine.get_engine()
    return engine


# {dialect} and {top_k} are placeholders substituted via str.format/replace in
# get_sql_chain, not by LangChain's create_sql_agent.

# Dual money metrics + bare-"revenue" clarify. Format-safe (no braces).
# Invoiced customer totals use bills; ledger revenue queries use journals.
REVENUE_METRIC_RULES = """
MONEY METRICS (two different numbers — never treat them as the same):
- "invoiced value" / "invoiced sales" / "invoice total(s)" -> SUM of Invoice bill_items using
  the per-line invoice formula above (bills WHERE type = 'Invoice'). Date filters use
  bills.created_at unless the question names another bill date.
- "ledger revenue" / "accounting revenue" / "journal revenue" -> journals joined to accounts,
  SUM(credit - debit) over accounts whose account_type is OPERATING_REVENUE or
  NON_OPERATING_REVENUE; the date is journals.post_date. Never sum bills/bill_items for
  ledger revenue.
- Bare / ambiguous "revenue" (no cue that it is invoiced sales vs ledger/accounting): ask a
  clarifying question which metric they mean. Do not guess. When clarifying, reply with
  exactly one line starting with CLARIFY: then the question (example:
  CLARIFY: Do you mean ledger revenue or invoiced sales?). Do not run SQL until the user
  answers. If the question already says invoiced/invoice sales or ledger/accounting/journals,
  use that metric — do not clarify.
"""

# Finished-but-unbilled jobs rules. Format-safe.
JOBS_FINISHED_UNBILLED_RULES = """
JOB BILLING STATUS:
- "finished but not invoiced" / "finished unbilled" / "haven't been invoiced yet" (jobs) ->
  work_orders whose status is FINISHED and that have no linked bill (bill_id is empty).
  Do not invent delivery-date "delayed" logic for unbilled questions — delayed delivery
  tracking columns are empty in this database.
"""

# After is_safe_select rejects non-SELECT, steer recovery via thread schema or CLARIFY.
ILLEGAL_SQL_RECOVERY_RULES = """
TOOL ERROR RECOVERY (when sql_db_query returns Error: only SELECT… or DML/DDL rejected):
- Do NOT retry SHOW COLUMNS, SHOW TABLES, information_schema, DROP, ALTER, or other non-SELECT
  discovery or DDL — those will be rejected again (fail-closed guard).
- Use the table and column names already in this conversation thread to write a valid SELECT.
- If you still cannot write a precise SELECT from thread context, reply with CLARIFY: and your
  question — do not emit another rejected query or finish without an answer.
"""


SQL_AGENT_PREFIX = registry.assemble("sql_agent")


# Injected ONLY on the escalation (gpt-4.1) path. Sharing this rule across the
# base prompt dilutes it enough to regress unrelated cases. Payment-status
# questions escalate via the router, so this still reaches the model that
# answers them. Format-safe (no braces).
ESCALATED_PAYMENT_RULES = """

PAYMENT-STATUS DEFINITIONS (apply precisely):
- "fully paid" / "paid in full": keep invoices whose remainder (value MINUS payments) is
  within a cent of zero — ABS(value - payments) <= 0.01. This EXCLUDES "overpaid" invoices
  (payments exceed value); "overpaid" and "partially paid" are SEPARATE states."""


# Injected ONLY on the gpt-4.1 escalation path, beside ESCALATED_PAYMENT_RULES. Adds
# only the two facts the base prefix is missing: the standing-balance reading of
# "who owes the most", and the invoice_number bill identifier. The value-payments
# formula + quotation=type='Quotation' are already in SQL_AGENT_PREFIX, so they are
# not restated. Gated (shared prose dilutes mini). Format-safe (no braces). Says
# nothing about filtering grain (per-invoice vs customer) — the base prefix's
# per-row rule is correct there.
ESCALATED_FINANCIAL_RULES = """

RECEIVABLE & BILL-IDENTITY DEFINITIONS (these refine the base rules above):
- "who owes the most" / a customer's outstanding receivable is a STANDING BALANCE — a snapshot
  of all currently-unpaid invoices. A time phrase like "this month" does NOT restrict invoices
  by creation date; report the standing balance across all unpaid invoices.
- Refer to a specific bill (invoice or quotation) by its invoice_number column. There is no
  "reference" or "number" column on bills."""


# Injected only when needs_cardinality_rules(question). Format-safe (no braces).
# No eval-case IDs or gold SQL — pattern language only.
CARDINALITY_RULES = """

CARDINALITY (answer grain — follow precisely):
- Singular asks ("the top/largest/biggest X", "which customer/invoice/…"): return exactly ONE
  row (use LIMIT 1). Do not return a ranked multi-row list.
- Count asks ("how many", "count", "number of"): return a single aggregate COUNT (or
  equivalent), not a row-list of matching entities."""


def build_sql_database(
    access_tiers: list[str] | TierScope,
    role: str,
    *,
    strict_tier_scope: bool = False,
    scope: TierScope | None = None,
) -> tuple[AnonymizingSQLDatabase, SqlAnonymizer]:
    """Build the (de)anonymizing DB the SQL agent reads through.

    Returned so the eval harness can reuse the SAME instance for scoring — token
    literals in the agent's SQL only round-trip through the anonymizer that minted them.
    """
    if isinstance(access_tiers, TierScope):
        tier_scope = access_tiers
    elif scope is not None:
        tier_scope = scope
    elif strict_tier_scope:
        tier_scope = TierScope.exact(access_tiers)
    else:
        tier_scope = TierScope.wildcard(access_tiers)

    allowed_tables = get_allowed_tables(tier_scope)
    assert allowed_tables, "tier policy yielded zero tables — TIER_TABLES misconfigured"

    anonymizer = SqlAnonymizer(
        query_id=current_query_id(),
        role=role,
        scope=tier_scope,
        ner_enabled=settings.redaction_enabled,
    )

    # Shared, pre-reflected schema (request-independent); fresh wrapper + anonymizer
    # per request so the PII token map is never shared across requests.
    resolved_engine = _resolve_engine()
    metadata = get_reflected_metadata(resolved_engine, allowed_tables)
    db = AnonymizingSQLDatabase(
        resolved_engine,
        metadata=metadata,
        include_tables=allowed_tables,
        # Schema introspection must never sample live rows ahead of row-level
        # authorization. 0 still yields column names + types via get_table_info().
        sample_rows_in_table_info=0,
        # metadata is already fully reflected → skip __init__'s reflect() round-trip.
        # get_table_info won't lazily reflect either (all usable tables are present).
        lazy_table_reflection=True,
        anonymizer=anonymizer,
    )
    return db, anonymizer


def _compile_anonymizing_agent(
    db: AnonymizingSQLDatabase,
    anonymizer: SqlAnonymizer | None,
    *,
    chat_deployment: str | None,
    route_context: RouteContext | None = None,
    route_reason: str | None,
    today: date | None,
    rules_block: str | None,
    episodic_block: str | None,
    question: str | None = None,
    controls: OpenRouterControls | None = None,
    deepseek_direct_controls: DeepSeekDirectControls | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    request_timeout_s: float | None = None,
    use_responses_api: bool = False,
    principal=None,
    row_policy: Any,
    allow_eval_fixture: bool = False,
    run_cost_ceiling_usd: float | None = None,
    run_price_fn: Callable[[int, int], float] | None = None,
) -> AnonymizingSqlAgent:
    """Compile the bare AnonymizingSqlAgent. Rules/episodic are concatenated,
    NEVER passed through str.format (a literal brace would raise KeyError).

    ``row_policy`` is required (real or eval fixture). The ask flow denies via
    ``ensure_scoped_sql_access`` before this path; missing/``None`` → TypeError
    forwarded from ``build_sql_graph``.

    ``run_cost_ceiling_usd``/``run_price_fn`` are eval-only (default ``None``, so
    production behaviour is unchanged) -- see ``run_case``.
    """
    # Explicit chat_deployment overrides (CLI/eval) skip RoutePolicy in the factory.
    # Ask passes route_context instead so catalog ModelSpec + controls apply.
    # chat_deployment=None always resolves to Azure for ModelPurpose.sql_agent
    # (app/providers/route_settings.py), so only a non-None value needs checking.
    if chat_deployment is not None:
        spec = resolve_model_spec(chat_deployment, settings)
        if spec.credential_source == "openrouter" and controls is None:
            raise PolicyViolationError(
                purpose=ModelPurpose.sql_agent.value,
                credential_source="openrouter",
                environment=settings.environment,
                model=chat_deployment,
                detail="SQL agent reached an OpenRouter deployment without explicit controls",
            )
        if spec.credential_source == "deepseek_direct" and deepseek_direct_controls is None:
            raise PolicyViolationError(
                purpose=ModelPurpose.sql_agent.value,
                credential_source="deepseek_direct",
                environment=settings.environment,
                model=chat_deployment,
                detail=(
                    "SQL agent reached a DeepSeek-direct deployment without explicit "
                    "deepseek_direct_controls"
                ),
            )
    llm = get_chat_model(
        purpose=ModelPurpose.sql_agent,
        temperature=0,
        deployment=None if route_context is not None else chat_deployment,
        route_context=route_context,
        controls=controls,
        deepseek_direct_controls=deepseek_direct_controls,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        request_timeout_s=request_timeout_s,
        use_responses_api=use_responses_api,
    )
    today = today if today is not None else date.today()
    cardinality_question = question
    if cardinality_question is None and route_context is not None:
        cardinality_question = route_context.question

    ctx = SqlPromptContext(
        question=cardinality_question or "",
        today=today,
        is_escalated=route_reason == "hard_financial",
        dialect=db.dialect,
        top_k=settings.sql_tool_max_rows,
        episodic_block=episodic_block or "",
        caller_rules_block=rules_block or "",
    )
    system_content = render_rules(SQL_PROMPT_RULES, ctx)
    budget = SqlToolOutputBudget(
        max_rows=settings.sql_tool_max_rows,
        max_chars=settings.sql_tool_max_chars,
    )
    run_budget = SqlRunBudget(
        max_llm_calls=settings.sql_run_max_llm_calls,
        max_next_prompt_tokens=settings.sql_run_max_next_prompt_tokens,
        max_cumulative_cost_usd=run_cost_ceiling_usd,
        price_fn=run_price_fn,
    )
    graph = build_sql_graph(
        db,
        llm,
        system_content,
        tool_output_budget=budget,
        run_budget=run_budget,
        principal=principal,
        row_policy=row_policy,
        allow_eval_fixture=allow_eval_fixture,
        today=today,
        sql_dry_run_explain=settings.sql_dry_run_explain,
    )
    return AnonymizingSqlAgent(graph, anonymizer, run_budget=run_budget)


def get_sql_chain(
    access_tiers: list[str] | TierScope,
    role: str,
    *,
    scope: TierScope | None = None,
    db: AnonymizingSQLDatabase | None = None,
    anonymizer: SqlAnonymizer | None = None,
    chat_deployment: str | None = None,
    route_context: RouteContext | None = None,
    route_reason: str | None = None,
    today: date | None = None,
    rules_block: str | None = None,
    episodic_block: str | None = None,
    question: str | None = None,
    strict_tier_scope: bool = False,
    controls: OpenRouterControls | None = None,
    deepseek_direct_controls: DeepSeekDirectControls | None = None,
    reasoning_effort: str | None = None,
    verbosity: str | None = None,
    request_timeout_s: float | None = None,
    use_responses_api: bool = False,
    principal=None,
    row_policy: Any,
    allow_eval_fixture: bool = False,
    run_cost_ceiling_usd: float | None = None,
    run_price_fn: Callable[[int, int], float] | None = None,
):
    """Build a SQL agent that can query the billing database."""
    if db is None:
        db, anonymizer = build_sql_database(
            access_tiers, role, strict_tier_scope=strict_tier_scope, scope=scope
        )
    elif anonymizer is not None and getattr(db, "_anonymizer", None) is not anonymizer:
        raise ValueError("db and anonymizer must be wired to the same anonymizer instance")
    elif anonymizer is None:
        anonymizer = getattr(db, "_anonymizer", None)
        if anonymizer is None:
            raise ValueError("db has no anonymizer; pass anonymizer from build_sql_database()")

    agent = _compile_anonymizing_agent(
        db,
        anonymizer,
        chat_deployment=chat_deployment,
        route_context=route_context,
        route_reason=route_reason,
        today=today,
        rules_block=rules_block,
        episodic_block=episodic_block,
        question=question,
        controls=controls,
        deepseek_direct_controls=deepseek_direct_controls,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        request_timeout_s=request_timeout_s,
        use_responses_api=use_responses_api,
        principal=principal,
        row_policy=row_policy,
        allow_eval_fixture=allow_eval_fixture,
        run_cost_ceiling_usd=run_cost_ceiling_usd,
        run_price_fn=run_price_fn,
    )

    if anonymizer is not None:

        def _record_stats(span, _result) -> None:
            record_sql_anonymization(
                span,
                tokens_created=anonymizer.stats.tokens_created,
                redacted_values=anonymizer.stats.redacted_values,
                suppressed_values=anonymizer.stats.suppressed_values,
                anonymize_calls=anonymizer.stats.anonymize_calls,
                entities_detected=anonymizer.stats.entities_detected,
            )

        return wrap_with_span(agent, guardrails_sql_anonymize_span, on_success=_record_stats)

    return agent


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run a SQL agent query against the live DB.")
    parser.add_argument("question", nargs="+", help="Natural-language question")
    parser.add_argument(
        "--role",
        default="default",
        help=(
            "User role. admin/finance see real values for pseudonymize fields; "
            "redact/suppress always apply."
        ),
    )
    parser.add_argument(
        "--tiers",
        default="all,sales,customers",
        help="Comma-separated access tiers (mirrors what get_access_tiers would return).",
    )
    args = parser.parse_args()

    from app.eval.sql.agent.access import ScopedSqlPolicy, ensure_scoped_sql_access
    from app.providers.model_purpose import ModelPurpose
    from app.providers.route_policy import RouteContext, get_route_policy

    question = " ".join(args.question)
    tiers = [t.strip() for t in args.tiers.split(",")]

    print(f"Question: {question}")
    print(f"Role: {args.role}, Tiers: {tiers}\n")

    # Explicit CLI/eval/test exemption: a manual debug CLI invocation, never
    # production request traffic — see app/rag/sql_access.py for what this
    # fixture does and does not grant.
    ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())

    resolved = get_route_policy().resolve(
        ModelPurpose.sql_agent,
        RouteContext(question=question),
    )
    agent = get_sql_chain(
        tiers,
        args.role,
        chat_deployment=resolved.deployment,
        route_reason=resolved.route_reason,
        question=question,
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    result = agent.invoke({"input": question})
    print(f"\nAnswer: {result['output']}")
