"""Ordered prompt rules for the SQL agent."""

from dataclasses import dataclass
from datetime import date

from app.prompts.rules import PromptRule


@dataclass(frozen=True)
class SqlPromptContext:
    """Inputs required to assemble the SQL agent system prompt."""

    question: str
    today: date
    is_escalated: bool
    dialect: str
    top_k: int
    episodic_block: str
    caller_rules_block: str


@dataclass(frozen=True)
class _DateResolutionRules:
    name: str = "date_resolution"

    def block(self, ctx: SqlPromptContext, /) -> str:
        return (
            f"Today's date is {ctx.today:%Y-%m-%d} ({ctx.today:%A}). "
            "Resolve relative dates like 'today', 'this week', 'this month', and "
            "'this year' against it. Call the sql_calendar_windows tool to obtain "
            "inclusive ISO start/end bounds before writing date filters. When a "
            "relative date is ambiguous about which year, week, or period is meant, "
            "prefer the most recent period that fits Today's date. If it is still "
            "ambiguous after that, reply with CLARIFY: then your question — do not "
            "guess."
        )


@dataclass(frozen=True)
class _NamedDayRules:
    name: str = "named_day"

    def block(self, ctx: SqlPromptContext, /) -> str:
        from app.eval.sql.agent.date_windows import (
            classify_named_day_intent,
            named_day_rules_block,
        )

        return named_day_rules_block(classify_named_day_intent(ctx.question))


@dataclass(frozen=True)
class _FutureDateRules:
    name: str = "future_date"

    def block(self, ctx: SqlPromptContext, /) -> str:
        from app.eval.sql.agent.date_windows import (
            future_date_policy,
            future_date_rules_block,
        )

        return future_date_rules_block(future_date_policy(ctx.question, ctx.today))


@dataclass(frozen=True)
class _WinnerVerifyRules:
    name: str = "winner_verify"

    def block(self, ctx: SqlPromptContext, /) -> str:
        from app.eval.sql.agent.winner_verify import (
            needs_winner_verify,
            winner_verify_rules_block,
        )

        return winner_verify_rules_block(needs_winner_verify(ctx.question))


@dataclass(frozen=True)
class _CallerRules:
    name: str = "caller_rules"

    def block(self, ctx: SqlPromptContext, /) -> str:
        return ctx.caller_rules_block


@dataclass(frozen=True)
class _SqlAgentPrefix:
    name: str = "sql_agent_prefix"

    def block(self, ctx: SqlPromptContext, /) -> str:
        from app.prompts.registry import registry

        return registry.assemble("sql_agent").format(dialect=ctx.dialect, top_k=ctx.top_k)


_ESCALATED_PAYMENT_RULES = """
PAYMENT-STATUS DEFINITIONS (apply precisely):
- "fully paid" / "paid in full": keep invoices whose remainder (value MINUS payments) is
  within a cent of zero — ABS(value - payments) <= 0.01. This EXCLUDES "overpaid" invoices
  (payments exceed value); "overpaid" and "partially paid" are SEPARATE states."""

_ESCALATED_FINANCIAL_RULES = """
RECEIVABLE & BILL-IDENTITY DEFINITIONS (these refine the base rules above):
- "who owes the most" / a customer's outstanding receivable is a STANDING BALANCE — a snapshot
  of all currently-unpaid invoices. A time phrase like "this month" does NOT restrict invoices
  by creation date; report the standing balance across all unpaid invoices.
- Refer to a specific bill (invoice or quotation) by its invoice_number column. There is no
  "reference" or "number" column on bills."""


@dataclass(frozen=True)
class _EscalatedRules:
    name: str = "escalated_rules"

    def block(self, ctx: SqlPromptContext, /) -> str:
        if not ctx.is_escalated:
            return ""
        return _ESCALATED_PAYMENT_RULES + _ESCALATED_FINANCIAL_RULES


_CARDINALITY_RULES = """
CARDINALITY (answer grain — follow precisely):
- Singular asks ("the top/largest/biggest X", "which customer/invoice/…"): return exactly ONE
  row (use LIMIT 1). Do not return a ranked multi-row list.
- Count asks ("how many", "count", "number of"): return a single aggregate COUNT (or
  equivalent), not a row-list of matching entities."""


@dataclass(frozen=True)
class _CardinalityRules:
    name: str = "cardinality"

    def block(self, ctx: SqlPromptContext, /) -> str:
        from app.rag.model_router import needs_cardinality_rules

        if not needs_cardinality_rules(ctx.question):
            return ""
        return _CARDINALITY_RULES


@dataclass(frozen=True)
class _EpisodicRules:
    name: str = "episodic"

    def block(self, ctx: SqlPromptContext, /) -> str:
        return ctx.episodic_block


SQL_PROMPT_RULES: tuple[PromptRule, ...] = (
    _DateResolutionRules(),
    _NamedDayRules(),
    _FutureDateRules(),
    _WinnerVerifyRules(),
    _CallerRules(),
    _SqlAgentPrefix(),
    _EscalatedRules(),
    _CardinalityRules(),
    _EpisodicRules(),
)
