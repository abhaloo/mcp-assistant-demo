"""Customer Order Detail Adapter and Status Authority.

Provides canonical status validation, tenant isolation, and reconciliation of
related jobs and invoices without parent row fan-out or duplication.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from app.business_query.record_detail.read_template import cap_ids

OrderStatus = Literal["CREATED", "ACTIVE", "FINISHED", "CANCELLED"]

VALID_ORDER_STATUSES: frozenset[str] = frozenset({"CREATED", "ACTIVE", "FINISHED", "CANCELLED"})

ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"ACTIVE", "CANCELLED"},
    "ACTIVE": {"FINISHED", "CANCELLED"},
    "FINISHED": set(),
    "CANCELLED": set(),
}


class InvalidOrderStatusError(ValueError):
    """Raised when an order has an invalid or unrecognized stored status."""


def validate_order_status_transition(current_status: str, target_status: str) -> bool:
    """Check whether a transition between two order lifecycle states is valid."""
    allowed = ORDER_STATUS_TRANSITIONS.get(current_status, set())
    return target_status in allowed


class RelatedJob(BaseModel):
    """Child Job reference linked to a Customer Order."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    entity_id: int
    order_id: int
    job_number: str | None = None
    title: str | None = None
    status: str | None = None


class RelatedInvoice(BaseModel):
    """Child Invoice reference linked to a Customer Order."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    entity_id: int
    order_id: int
    invoice_number: str | None = None
    status: str | None = None
    total_amount: float | None = None


class CustomerOrderDetail(BaseModel):
    """Canonical Customer Order detail projection with reconciled child relations."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    entity_id: int
    order_number: str
    customer_id: int
    status: OrderStatus
    created_at: datetime
    jobs: list[RelatedJob] = Field(default_factory=list)
    invoices: list[RelatedInvoice] = Field(default_factory=list)


class CustomerOrderDetailAdapter:
    """Bounded, tenant-isolated Customer Order detail and child relation reader."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_order_detail(
        self,
        order_id: int,
        entity_id: int,
    ) -> CustomerOrderDetail | None:
        """Fetch a single customer order with its related jobs and invoices."""
        results = self.read_orders(order_ids=[order_id], entity_id=entity_id)
        return results[0] if results else None

    def read_orders(
        self,
        order_ids: Sequence[int],
        entity_id: int,
    ) -> list[CustomerOrderDetail]:
        """Fetch customer orders and reconcile child collections at parent grain."""
        if not order_ids:
            return []

        capped_ids = cap_ids(order_ids)
        if not capped_ids:
            return []

        orders_stmt = sa.text(
            "SELECT id, entity_id, order_number, customer_id, status, created_at "
            "FROM orders "
            "WHERE entity_id = :entity_id "
            "AND id IN :order_ids"
        ).bindparams(
            sa.bindparam("order_ids", expanding=True),
        )

        jobs_stmt = sa.text(
            "SELECT id, entity_id, order_id, job_number, title, status "
            "FROM jobs "
            "WHERE entity_id = :entity_id "
            "AND order_id IN :order_ids"
        ).bindparams(
            sa.bindparam("order_ids", expanding=True),
        )

        invoices_stmt = sa.text(
            "SELECT id, entity_id, order_id, invoice_number, status, total_amount "
            "FROM invoices "
            "WHERE entity_id = :entity_id "
            "AND order_id IN :order_ids"
        ).bindparams(
            sa.bindparam("order_ids", expanding=True),
        )

        with self._engine.connect() as conn:
            orders_result = conn.execute(
                orders_stmt,
                {
                    "entity_id": entity_id,
                    "order_ids": tuple(capped_ids),
                },
            )
            order_rows = orders_result.mappings().fetchall()

            if not order_rows:
                return []

            matched_order_ids = tuple(r["id"] for r in order_rows)

            jobs_result = conn.execute(
                jobs_stmt,
                {
                    "entity_id": entity_id,
                    "order_ids": matched_order_ids,
                },
            )
            job_rows = jobs_result.mappings().fetchall()

            invoices_result = conn.execute(
                invoices_stmt,
                {
                    "entity_id": entity_id,
                    "order_ids": matched_order_ids,
                },
            )
            invoice_rows = invoices_result.mappings().fetchall()

        jobs_by_order: dict[int, list[RelatedJob]] = defaultdict(list)
        for jr in job_rows:
            jobs_by_order[int(jr["order_id"])].append(
                RelatedJob(
                    id=int(jr["id"]),
                    entity_id=int(jr["entity_id"]),
                    order_id=int(jr["order_id"]),
                    job_number=str(jr["job_number"]) if jr["job_number"] is not None else None,
                    title=str(jr["title"]) if jr["title"] is not None else None,
                    status=str(jr["status"]) if jr["status"] is not None else None,
                )
            )

        invoices_by_order: dict[int, list[RelatedInvoice]] = defaultdict(list)
        for ir in invoice_rows:
            invoices_by_order[int(ir["order_id"])].append(
                RelatedInvoice(
                    id=int(ir["id"]),
                    entity_id=int(ir["entity_id"]),
                    order_id=int(ir["order_id"]),
                    invoice_number=(
                        str(ir["invoice_number"]) if ir["invoice_number"] is not None else None
                    ),
                    status=str(ir["status"]) if ir["status"] is not None else None,
                    total_amount=(
                        float(ir["total_amount"]) if ir["total_amount"] is not None else None
                    ),
                )
            )

        orders: list[CustomerOrderDetail] = []
        for r in order_rows:
            order_id = int(r["id"])
            raw_status = str(r["status"])
            if raw_status not in VALID_ORDER_STATUSES:
                raise InvalidOrderStatusError(
                    f"Stored order status '{raw_status}' for order {order_id} "
                    f"is not in valid vocabulary {sorted(VALID_ORDER_STATUSES)}"
                )

            created_at_val = r["created_at"]
            if isinstance(created_at_val, str):
                created_at_val = datetime.fromisoformat(created_at_val)

            orders.append(
                CustomerOrderDetail(
                    id=order_id,
                    entity_id=int(r["entity_id"]),
                    order_number=str(r["order_number"]),
                    customer_id=int(r["customer_id"]),
                    status=raw_status,  # type: ignore[arg-type]
                    created_at=created_at_val,
                    jobs=jobs_by_order.get(order_id, []),
                    invoices=invoices_by_order.get(order_id, []),
                )
            )

        return orders

    def read_order_details_mapping(
        self,
        order_ids: Sequence[int],
        entity_id: int,
    ) -> dict[int, CustomerOrderDetail]:
        """Fetch order details and return mapping keyed by order ID."""
        orders = self.read_orders(order_ids, entity_id)
        return {o.id: o for o in orders}
