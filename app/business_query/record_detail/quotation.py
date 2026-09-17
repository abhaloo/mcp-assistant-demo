"""Open and converted quotation snapshot reads.

Extends the invoice bill-fact adapter with quotation-lifecycle queries that
sit outside the generic bill/line-item shape: open (not yet converted or
closed) quotations, and quotations that have converted to orders.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa

from app.auth import Principal
from app.business_query.authorize.scoping import ScopeDenied
from app.business_query.record_detail.invoice import (
    _CLOSED_OR_TERMINAL_STATUSES,
    _CONVERTED_STATUSES,
    BillDetail,
    InvoiceDetailAdapter,
    _parse_datetime,
)
from app.business_query.record_detail.read_template import cap_ids


class QuotationDetailAdapter(InvoiceDetailAdapter):
    """Quotation-lifecycle reader built on the invoice bill-fact permission model.

    Subclasses InvoiceDetailAdapter to reuse its engine handle and permission
    checks (`_assert_permission`) instead of duplicating them; a
    QuotationDetailAdapter therefore also exposes `read_bill_details` and
    `read_line_items` unchanged.
    """

    def _read_bills(
        self,
        principal: Principal,
        parent_ids: Sequence[int | str] | None,
        *,
        converted: bool,
    ) -> list[BillDetail]:
        """Shared bill-fact query behind the two quotation-lifecycle reads.

        `converted=True` selects quotations already turned into orders
        (`_CONVERTED_STATUSES`); `converted=False` selects quotations that
        are neither converted nor closed/terminal — the same status sets
        `read_bill_details` classifies with Python-side, so the SQL filter
        and the `is_open_quotation`/`is_converted_order` flags can never
        drift apart from what was actually queried.
        """
        if parent_ids is not None and not parent_ids:
            return []

        norm_resource = "quotation"
        self._assert_permission(principal, norm_resource)

        entity_id = principal.entity_id
        if entity_id is None and not principal.cross_entity:
            raise ScopeDenied()

        if converted:
            status_clause = "status IN :statuses"
            statuses = tuple(_CONVERTED_STATUSES)
        else:
            status_clause = "status NOT IN :statuses"
            statuses = tuple(_CONVERTED_STATUSES | _CLOSED_OR_TERMINAL_STATUSES)

        where_clauses = ["type = 'Quotation'", status_clause]
        params: dict[str, Any] = {"statuses": statuses}
        bind_params = [sa.bindparam("statuses", expanding=True)]

        if entity_id is not None:
            where_clauses.append("entity_id = :entity_id")
            params["entity_id"] = entity_id

        if parent_ids is not None:
            capped_ids = cap_ids(parent_ids)
            if not capped_ids:
                return []
            where_clauses.append("id IN :parent_ids")
            params["parent_ids"] = tuple(capped_ids)
            bind_params.append(sa.bindparam("parent_ids", expanding=True))

        query_sql = (
            "SELECT id, invoice_number, customer_id, customer_name, type, payable, "
            "status, created_at, due_date, items_total, cash_received, "
            "receivable_settlement, outstanding, currency_code, days_past_due, entity_id "
            "FROM ai_v1_bq_bill_fact "
            f"WHERE {' AND '.join(where_clauses)} "
            "ORDER BY id ASC"
        )
        stmt = sa.text(query_sql).bindparams(*bind_params)

        with self._engine.connect() as conn:
            result = conn.execute(stmt, params)
            rows = result.mappings().fetchall()

        return [
            BillDetail(
                id=int(r["id"]),
                invoice_number=r.get("invoice_number"),
                customer_id=int(r["customer_id"]) if r.get("customer_id") is not None else None,
                customer_name=r.get("customer_name"),
                resource_type=norm_resource,
                type=str(r.get("type") or "Quotation"),
                payable=r.get("payable"),
                status=str(r.get("status") or ""),
                is_open_quotation=not converted,
                is_converted_order=converted,
                created_at=_parse_datetime(r.get("created_at")),
                due_date=_parse_datetime(r.get("due_date")),
                items_total=r.get("items_total") if r.get("items_total") is not None else 0,
                cash_received=(r.get("cash_received") if r.get("cash_received") is not None else 0),
                receivable_settlement=(
                    r.get("receivable_settlement")
                    if r.get("receivable_settlement") is not None
                    else 0
                ),
                outstanding=r.get("outstanding") if r.get("outstanding") is not None else 0,
                currency_code=str(r.get("currency_code") or "TZS"),
                days_past_due=(
                    int(r["days_past_due"]) if r.get("days_past_due") is not None else None
                ),
                entity_id=int(r["entity_id"]),
            )
            for r in rows
        ]

    def read_open_quotations(
        self,
        principal: Principal,
        parent_ids: Sequence[int | str] | None = None,
    ) -> list[BillDetail]:
        """Fetch open quotation snapshot records, excluding converted and closed quotes."""
        return self._read_bills(principal, parent_ids, converted=False)

    def read_converted_quotations(
        self,
        principal: Principal,
        parent_ids: Sequence[int | str] | None = None,
    ) -> list[BillDetail]:
        """Fetch quotations that have been converted to orders."""
        return self._read_bills(principal, parent_ids, converted=True)
