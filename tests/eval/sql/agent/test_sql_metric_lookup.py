"""Unit tests for the billing metric resolver."""

from __future__ import annotations

import pytest

from app.eval.sql.agent.metric_lookup import (
    ClarifyRequired,
    MetricHit,
    SoftAbsent,
    format_metric_inject_block,
    resolve_metric,
)
from app.rag.metrics.billing_metrics import load_catalog


@pytest.fixture(scope="module")
def catalog() -> list[dict]:
    return load_catalog()


def test_alias_hit_returns_definition_payload(catalog: list[dict]) -> None:
    result = resolve_metric("how many invoices have been fully paid this month?", catalog=catalog)
    assert isinstance(result, MetricHit)
    assert result.metric_id == "fully_paid"
    assert "remainder" in result.definition.lower()
    assert "overpaid" in result.caveats.lower()
    block = format_metric_inject_block(result)
    assert "fully_paid" in block
    assert result.definition in block


def test_non_metric_question_is_soft_absent(catalog: list[dict]) -> None:
    result = resolve_metric("what is the widget coefficient?", catalog=catalog)
    assert isinstance(result, SoftAbsent)
    assert result.clarify_required is False


def test_ordinary_sql_question_without_metric_vocab_is_soft_absent(
    catalog: list[dict],
) -> None:
    result = resolve_metric("how many products do we have?", catalog=catalog)
    assert isinstance(result, SoftAbsent)


@pytest.mark.parametrize(
    "question",
    [
        "how many orders?",
        "orders created this week",
        "invoice count by month",
        "can we pay vendors faster?",
        "balance sheet accounts",
    ],
)
def test_broad_ask_questions_without_metric_intent_are_soft_absent(
    catalog: list[dict], question: str
) -> None:
    result = resolve_metric(question, catalog=catalog)
    assert isinstance(result, SoftAbsent)
    assert result.clarify_required is False


@pytest.mark.parametrize(
    "question",
    [
        "show me revenue",
        "what is our total revenue?",
        "report revenue for last quarter",
    ],
)
def test_metric_intent_zero_alias_hits_clarify(catalog: list[dict], question: str) -> None:
    result = resolve_metric(question, catalog=catalog)
    assert isinstance(result, ClarifyRequired)
    assert result.clarify_required is True
    assert result.candidate_ids == ()


def test_ambiguous_multi_alias_hit_clarifies(catalog: list[dict]) -> None:
    result = resolve_metric("compare overdue and outstanding receivable totals", catalog=catalog)
    assert isinstance(result, ClarifyRequired)
    assert result.clarify_required is True
    assert len(result.candidate_ids) >= 2
    assert "overdue" in result.candidate_ids
    assert "outstanding" in result.candidate_ids


def test_unique_revenue_invoiced_alias_hits(catalog: list[dict]) -> None:
    result = resolve_metric("sum invoiced sales for April", catalog=catalog)
    assert isinstance(result, MetricHit)
    assert result.metric_id == "revenue_invoiced"


def test_unique_revenue_ledger_alias_hits(catalog: list[dict]) -> None:
    result = resolve_metric("ledger revenue by month", catalog=catalog)
    assert isinstance(result, MetricHit)
    assert result.metric_id == "revenue_ledger"


def test_unbilled_jobs_alias_hits(catalog: list[dict]) -> None:
    result = resolve_metric("list finished unbilled jobs", catalog=catalog)
    assert isinstance(result, MetricHit)
    assert result.metric_id == "unbilled_jobs"


@pytest.mark.parametrize(
    "question",
    [
        "show our accounts receivable aging by bucket",
        "AR aging by bucket",
        "receivable aging report",
        "what is our aging schedule",
        "AR aging",
        "aging by bucket",
        "receivable aging",
    ],
)
def test_aging_questions_resolve_to_the_ar_aging_metric(catalog: list[dict], question: str) -> None:
    """Aging asks must resolve directly to ar_aging, not trigger a clarify,
    despite "aging" overlapping with the overdue/outstanding aliases.
    """
    result = resolve_metric(question, catalog=catalog)
    assert isinstance(result, MetricHit), f"{question!r} -> {result}"
    assert result.metric_id == "ar_aging"
    assert "due" in result.definition.lower()


def test_ar_aging_definition_carries_the_contract_buckets(catalog: list[dict]) -> None:
    """The ar_aging definition must state the bucket boundaries, since
    resolve_metric answers directly without a clarify step to ask for them.
    """
    result = resolve_metric("show our accounts receivable aging by bucket", catalog=catalog)
    assert isinstance(result, MetricHit)
    text = result.definition.lower()
    for band in ("1-30", "31-60", "61-90", "91+"):
        assert band in text, f"missing bucket {band}"


def test_longest_matching_alias_wins_over_a_contained_one(catalog: list[dict]) -> None:
    """'accounts receivable aging' must beat 'accounts receivable' (outstanding)."""
    result = resolve_metric("accounts receivable aging", catalog=catalog)
    assert isinstance(result, MetricHit)
    assert result.metric_id == "ar_aging"


def test_overdue_still_resolves_after_losing_the_aging_alias(catalog: list[dict]) -> None:
    result = resolve_metric("which invoices are overdue", catalog=catalog)
    assert isinstance(result, MetricHit)
    assert result.metric_id == "overdue"


def test_open_orders_uses_stored_status_vocabulary(catalog: list[dict]) -> None:
    """customer_orders.status stores CREATED; NEW/IN_PROGRESS are display-only.

    Oracle: billing migration create_customer_orders_table (status varchar
    DEFAULT 'CREATED') plus CustomerOrder::getComputedStatusAttribute, which
    derives NEW/IN_PROGRESS and never writes them. Filtering on those tokens
    returns zero rows -- this asserts they never reach the model.
    """
    result = resolve_metric("how many open orders do we have", catalog=catalog)
    assert isinstance(result, MetricHit)
    assert result.metric_id == "open_orders"
    payload = " ".join([result.definition, result.caveats, *result.column_hints])
    assert "IN_PROGRESS" not in payload
    assert "NEW" not in payload.replace("NEWS", "")
    hints = " ".join(result.column_hints)
    assert "FINISHED" in hints and "CANCELLED" in hints
