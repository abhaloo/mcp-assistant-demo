"""Vendored B0 view columns and expression grammar token definitions."""

from __future__ import annotations

import re

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

VIEW_COLUMNS: dict[str, frozenset[str]] = {
    "ai_v1_bq_bill_fact": frozenset(
        {
            "id",
            "invoice_number",
            "customer_id",
            "customer_name",
            "customer_order_id",
            "customer_order_number",
            "type",
            "payable",
            "status",
            "created_at",
            "due_date",
            "items_total",
            "cash_received",
            "receivable_settlement",
            "outstanding",
            "currency_code",
            "days_past_due",
            "entity_id",
        }
    ),
    "ai_v1_bq_order_fact": frozenset(
        {
            "id",
            "order_number",
            "customer_id",
            "customer_name",
            "description",
            "status",
            "computed_status",
            "created_at",
            "entity_id",
        }
    ),
    "ai_v1_bq_job_fact": frozenset(
        {
            "id",
            "customer_order_id",
            "customer_order_number",
            "customer_id",
            "customer_name",
            "title",
            "work_number",
            "ordered_qty",
            "effective_bill_id",
            "status",
            "department_id",
            "department_name",
            "created_at",
            "entity_id",
        }
    ),
    "ai_v1_bq_customer_fact": frozenset({"id", "name", "has_orders", "created_at", "entity_id"}),
    "ai_v1_bq_supplier_fact": frozenset({"id", "name", "created_at", "entity_id"}),
    "ai_v1_bq_ledger_entry": frozenset(
        {
            "id",
            "bill_id",
            "account_type",
            "debit",
            "credit",
            "currency_code",
            "post_date",
            "entity_id",
        }
    ),
    "ai_v1_bq_bill_item_fact": frozenset(
        {
            "id",
            "bill_id",
            "invoice_number",
            "product_id",
            "product_name",
            "quantity",
            "price",
            "discount",
            "tax",
            "item_revenue",
            "bill_type",
            "bill_payable",
            "bill_created_at",
            "currency_code",
            "entity_id",
        }
    ),
    "ai_v1_bq_inventory_fact": frozenset(
        {
            "id",
            "supplier_id",
            "job_id",
            "type",
            "status",
            "inventory_date",
            "created_at",
            "supplier_name",
            "warehouse_name",
            "entity_id",
        }
    ),
    "ai_v1_bq_work_order_item_fact": frozenset(
        {
            "id",
            "work_order_id",
            "product_id",
            "product_name",
            "quantity",
            "used_at",
            "department_id",
            "entity_id",
        }
    ),
}

DETAIL_VIEW_COLUMNS: dict[str, frozenset[str]] = {
    "ai_v2_bq_job_print_fact": frozenset(
        {
            "id",
            "job_id",
            "work_order_id",
            "work_number",
            "title",
            "customer_id",
            "customer_order_id",
            "entity_id",
            "department_id",
            "department_name",
            "status",
            "created_at",
            "resource_type",
            "family_key",
            "attribute_key",
            "revision",
            "revision_hash",
            "value_kind",
            "typed_value",
            "value_text",
            "value_number",
            "value_boolean",
            "value_json",
            "display_value",
            "validation_state",
            "source",
            "provenance",
        }
    ),
    "ai_v2_bq_detail_attribute_fact": frozenset(
        {
            "id",
            "resource_type",
            "resource_id",
            "entity_id",
            "department_id",
            "family_key",
            "attribute_key",
            "revision",
            "revision_hash",
            "value_kind",
            "typed_value",
            "value_text",
            "value_number",
            "value_boolean",
            "value_json",
            "display_value",
            "validation_state",
            "source",
            "provenance",
            "raw_value",
            "source_fingerprint",
            "source_updated_at",
        }
    ),
}

_ALLOWED_FUNCTIONS = frozenset(
    {"SUM", "AVG", "MIN", "MAX", "COUNT", "COALESCE", "CASE", "WHEN", "THEN", "ELSE", "END"}
)
_SQL_KEYWORDS = frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "IN",
        "IS",
        "NULL",
        "AS",
    }
)
_BANNED_SUBSTRINGS = (
    ";",
    "--",
    "/*",
    "CURRENT_DATE",
    "INTERVAL",
    "DATEDIFF",
    "julianday",
)

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    |(?P<str>'(?:[^'\\])*')
    |(?P<num>\d+(?:\.\d+)?)
    |(?P<op><>|!=|<=|>=|=|<|>|\+|-|\*|/)
    |(?P<punct>[(),])
    |(?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    |(?P<bad>.)
    """,
    re.VERBOSE,
)
