"""Invoice, Quotation, Payable Quotation, and Credit Note detail adapter.

Provides:
- Distinct logical resource handling over ai_v1_bq_bill_fact.
- Line item grain preservation via ai_v1_bq_bill_item_fact.
- Permission enforcement (view invoice, view quotation, view payable quotation).
- Multi-entity tenant isolation and bounding guards.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.engine import Engine

from app.auth import Principal
from app.business_query.authorize.scoping import ScopeDenied
from app.business_query.record_detail.read_template import cap_ids

logger = logging.getLogger(__name__)

_OPEN_STATUSES = frozenset({"OPEN", "PENDING", "DRAFT", "ACTIVE", "SUBMITTED", "PENDING APPROVAL"})
_CONVERTED_STATUSES = frozenset({"CONVERTED", "ACCEPTED", "ORDERED", "INVOICED"})
_CLOSED_OR_TERMINAL_STATUSES = frozenset({"CANCELLED", "EXPIRED", "REJECTED"})

LogicalResourceType = Literal["invoice", "quotation", "payable_quotation", "credit_note"]


def _parse_datetime(val: Any) -> datetime | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            pass
    return None


class InvoiceLineItem(BaseModel):
    """Detailed line item preserving individual item grain."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: int
    bill_id: int
    product_id: int | None = None
    product_name: str
    quantity: float | int | Decimal = 0
    item_revenue: float | int | Decimal = 0
    bill_type: str
    bill_payable: str | None = None
    currency_code: str = "TZS"
    entity_id: int
    bill_created_at: datetime | None = None

    @field_validator("bill_created_at", mode="before")
    @classmethod
    def _coerce_datetime(cls, val: Any) -> Any:
        return _parse_datetime(val) if isinstance(val, str) else val


class BillDetail(BaseModel):
    """Authorized typed bill detail representing an invoice, quotation, or credit note."""

    model_config = ConfigDict(strict=True, extra="forbid")

    id: int
    invoice_number: str | None = None
    customer_id: int | None = None
    customer_name: str | None = None
    resource_type: str
    type: str
    payable: str | bool | None = None
    status: str
    is_open_quotation: bool = False
    is_converted_order: bool = False
    created_at: datetime | None = None
    due_date: datetime | None = None
    items_total: float | int | Decimal = 0
    cash_received: float | int | Decimal = 0
    receivable_settlement: float | int | Decimal = 0
    outstanding: float | int | Decimal = 0
    currency_code: str = "TZS"
    days_past_due: int | None = None
    entity_id: int
    line_items: list[InvoiceLineItem] = Field(default_factory=list)

    @field_validator("created_at", "due_date", mode="before")
    @classmethod
    def _coerce_datetime(cls, val: Any) -> Any:
        return _parse_datetime(val) if isinstance(val, str) else val


class InvoiceDetailAdapter:
    """Distinct logical resource adapter for Invoices, Quotations, and Line Details."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @staticmethod
    def _normalize_resource_type(resource_type: str) -> str:
        norm = resource_type.strip().lower().replace(" ", "_")
        if norm in ("invoice", "invoices"):
            return "invoice"
        if norm in ("quotation", "quotations"):
            return "quotation"
        if norm in ("payable_quotation", "payable_quotations"):
            return "payable_quotation"
        if norm in ("credit_note", "credit_notes"):
            return "credit_note"
        return norm

    def _assert_permission(self, principal: Principal, resource: str) -> None:
        # Defense-in-depth: the type signature requires a Principal, but
        # Python does not enforce that at runtime, so a caller passing None
        # explicitly must still fail closed with ScopeDenied.
        if principal is None:
            raise ScopeDenied()

        norm = self._normalize_resource_type(resource)
        permissions = set(principal.permissions)

        if norm == "invoice":
            if "view invoice" not in permissions and "view bills" not in permissions:
                raise ScopeDenied()
        elif norm == "quotation":
            if "view quotation" not in permissions:
                raise ScopeDenied()
        elif norm == "payable_quotation":
            if "view payable quotation" not in permissions:
                raise ScopeDenied()
        elif norm == "credit_note":
            if "view invoice" not in permissions and "view credit note" not in permissions:
                raise ScopeDenied()
        else:
            raise ScopeDenied()

    @staticmethod
    def _resource_predicates(norm_resource: str) -> dict[str, Any]:
        if norm_resource == "invoice":
            return {"type": "Invoice"}
        if norm_resource == "quotation":
            return {"type": "Quotation"}
        if norm_resource == "payable_quotation":
            return {"type": "Payable Quotation", "payable": "Yes"}
        if norm_resource == "credit_note":
            return {"type": "Credit Note"}
        raise ScopeDenied()

    def read_line_items(
        self,
        principal: Principal,
        resource_type: str,
        bill_ids: Sequence[int | str],
    ) -> list[InvoiceLineItem]:
        """Fetch line items preserving individual grain for the given bill IDs."""
        if not bill_ids:
            return []

        norm_resource = self._normalize_resource_type(resource_type)
        self._assert_permission(principal, norm_resource)

        entity_id = principal.entity_id
        if entity_id is None and not principal.cross_entity:
            raise ScopeDenied()

        capped_ids = cap_ids(bill_ids)
        if not capped_ids:
            return []

        predicates = self._resource_predicates(norm_resource)
        bill_type = predicates["type"]
        bill_payable = predicates.get("payable")

        where_clauses = [
            "bill_id IN :bill_ids",
            "bill_type = :bill_type",
        ]
        params: dict[str, Any] = {
            "bill_ids": tuple(capped_ids),
            "bill_type": bill_type,
        }

        if entity_id is not None:
            where_clauses.append("entity_id = :entity_id")
            params["entity_id"] = entity_id

        if bill_payable is not None:
            where_clauses.append("bill_payable = :bill_payable")
            params["bill_payable"] = bill_payable

        query_sql = (
            "SELECT id, bill_id, product_id, product_name, quantity, item_revenue, "
            "bill_type, bill_payable, currency_code, entity_id, bill_created_at "
            "FROM ai_v1_bq_bill_item_fact "
            f"WHERE {' AND '.join(where_clauses)} "
            "ORDER BY id ASC"
        )
        stmt = sa.text(query_sql).bindparams(sa.bindparam("bill_ids", expanding=True))

        with self._engine.connect() as conn:
            result = conn.execute(stmt, params)
            rows = result.mappings().fetchall()

        items: list[InvoiceLineItem] = []
        for r in rows:
            items.append(
                InvoiceLineItem(
                    id=int(r["id"]),
                    bill_id=int(r["bill_id"]),
                    product_id=int(r["product_id"]) if r.get("product_id") is not None else None,
                    product_name=str(r["product_name"] or ""),
                    quantity=r.get("quantity") if r.get("quantity") is not None else 0,
                    item_revenue=r.get("item_revenue") if r.get("item_revenue") is not None else 0,
                    bill_type=str(r.get("bill_type") or bill_type),
                    bill_payable=r.get("bill_payable"),
                    currency_code=str(r.get("currency_code") or "TZS"),
                    entity_id=int(r["entity_id"]),
                    bill_created_at=_parse_datetime(r.get("bill_created_at")),
                )
            )
        return items

    def read_bill_details(
        self,
        principal: Principal,
        resource_type: str,
        parent_ids: Sequence[int | str],
        include_line_items: bool = False,
    ) -> list[BillDetail]:
        """Fetch bill details strictly scoped to logical resource and entity."""
        if not parent_ids:
            return []

        norm_resource = self._normalize_resource_type(resource_type)
        self._assert_permission(principal, norm_resource)

        entity_id = principal.entity_id
        if entity_id is None and not principal.cross_entity:
            raise ScopeDenied()

        capped_ids = cap_ids(parent_ids)
        if not capped_ids:
            return []

        predicates = self._resource_predicates(norm_resource)
        doc_type = predicates["type"]
        payable_req = predicates.get("payable")

        where_clauses = [
            "id IN :parent_ids",
            "type = :doc_type",
        ]
        params: dict[str, Any] = {
            "parent_ids": tuple(capped_ids),
            "doc_type": doc_type,
        }

        if entity_id is not None:
            where_clauses.append("entity_id = :entity_id")
            params["entity_id"] = entity_id

        if payable_req is not None:
            where_clauses.append("payable = :payable_req")
            params["payable_req"] = payable_req

        query_sql = (
            "SELECT id, invoice_number, customer_id, customer_name, type, payable, "
            "status, created_at, due_date, items_total, cash_received, "
            "receivable_settlement, outstanding, currency_code, days_past_due, entity_id "
            "FROM ai_v1_bq_bill_fact "
            f"WHERE {' AND '.join(where_clauses)} "
            "ORDER BY id ASC"
        )
        stmt = sa.text(query_sql).bindparams(sa.bindparam("parent_ids", expanding=True))

        with self._engine.connect() as conn:
            result = conn.execute(stmt, params)
            rows = result.mappings().fetchall()

        if not rows:
            return []

        retrieved_ids = [int(r["id"]) for r in rows]
        line_items_by_bill: dict[int, list[InvoiceLineItem]] = defaultdict(list)
        if include_line_items:
            all_line_items = self.read_line_items(principal, norm_resource, retrieved_ids)
            for item in all_line_items:
                line_items_by_bill[item.bill_id].append(item)

        details: list[BillDetail] = []
        for r in rows:
            bill_id = int(r["id"])
            status_str = str(r.get("status") or "").upper()
            is_open_quo = (
                norm_resource in ("quotation", "payable_quotation")
                and (
                    status_str in _OPEN_STATUSES
                    or status_str not in (_CONVERTED_STATUSES | _CLOSED_OR_TERMINAL_STATUSES)
                )
                and status_str not in _CONVERTED_STATUSES
                and status_str not in _CLOSED_OR_TERMINAL_STATUSES
            )
            is_conv_order = status_str in _CONVERTED_STATUSES

            details.append(
                BillDetail(
                    id=bill_id,
                    invoice_number=r.get("invoice_number"),
                    customer_id=(
                        int(r["customer_id"]) if r.get("customer_id") is not None else None
                    ),
                    customer_name=r.get("customer_name"),
                    resource_type=norm_resource,
                    type=str(r.get("type") or doc_type),
                    payable=r.get("payable"),
                    status=str(r.get("status") or ""),
                    is_open_quotation=is_open_quo,
                    is_converted_order=is_conv_order,
                    created_at=_parse_datetime(r.get("created_at")),
                    due_date=_parse_datetime(r.get("due_date")),
                    items_total=(r.get("items_total") if r.get("items_total") is not None else 0),
                    cash_received=(
                        r.get("cash_received") if r.get("cash_received") is not None else 0
                    ),
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
                    line_items=line_items_by_bill.get(bill_id, []),
                )
            )
        return details
