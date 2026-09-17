"""Customer Detail Adapter and Sensitivity Policy.

Provides bounded customer detail reads with strict tenant isolation and
permission-gated access control over sensitive contact and financial fields.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from app.auth import Principal
from app.business_query.authorize.scoping import ScopeDenied
from app.business_query.record_detail.read_template import cap_ids

PERMISSION_CUSTOMER_CONTACT = "view customer contact"
PERMISSION_CUSTOMER_FINANCIAL = "view customer financial"


class CustomerDetail(BaseModel):
    """Stable and permission-gated customer detail projection."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    entity_id: int
    name: str
    has_orders: bool = False
    created_at: datetime
    phone: str | None = None
    email: str | None = None
    tax_number: str | None = None
    balance_owing: float | None = None


class CustomerDetailAdapter:
    """Bounded, tenant-isolated Customer Detail observation and projection reader."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_customer_detail(
        self,
        customer_id: int,
        entity_id: int,
        principal: Principal | None = None,
    ) -> CustomerDetail | None:
        """Fetch a single customer detail record with tenant isolation and permission gating."""
        results = self.read_customers(
            customer_ids=[customer_id],
            entity_id=entity_id,
            principal=principal,
        )
        return results[0] if results else None

    def read_customers(
        self,
        customer_ids: Sequence[int],
        entity_id: int,
        principal: Principal | None = None,
    ) -> list[CustomerDetail]:
        """Fetch customer details for multiple IDs with tenant isolation and permission gating."""
        if principal is None:
            raise ScopeDenied()

        if not customer_ids:
            return []

        capped_ids = cap_ids(customer_ids)
        if not capped_ids:
            return []

        stmt = sa.text(
            "SELECT id, entity_id, name, phone, email, tax_number, "
            "balance_owing, has_orders, created_at "
            "FROM customers "
            "WHERE entity_id = :entity_id "
            "AND id IN :customer_ids"
        ).bindparams(
            sa.bindparam("customer_ids", expanding=True),
        )

        with self._engine.connect() as conn:
            result = conn.execute(
                stmt,
                {
                    "entity_id": entity_id,
                    "customer_ids": tuple(capped_ids),
                },
            )
            rows = result.mappings().fetchall()

        has_contact_perm = principal is not None and PERMISSION_CUSTOMER_CONTACT in (
            principal.permissions or []
        )
        has_financial_perm = principal is not None and PERMISSION_CUSTOMER_FINANCIAL in (
            principal.permissions or []
        )

        customers: list[CustomerDetail] = []
        for r in rows:
            created_at_val = r["created_at"]
            if isinstance(created_at_val, str):
                created_at_val = datetime.fromisoformat(created_at_val)

            # Disclose sensitive contact fields only if principal has required permission
            phone = str(r["phone"]) if has_contact_perm and r["phone"] is not None else None
            email = str(r["email"]) if has_contact_perm and r["email"] is not None else None

            # Disclose sensitive financial fields only if principal has required permission
            tax_number = (
                str(r["tax_number"]) if has_financial_perm and r["tax_number"] is not None else None
            )
            balance_owing = (
                float(r["balance_owing"])
                if has_financial_perm and r["balance_owing"] is not None
                else None
            )

            customers.append(
                CustomerDetail(
                    id=int(r["id"]),
                    entity_id=int(r["entity_id"]),
                    name=str(r["name"]),
                    has_orders=bool(r["has_orders"]),
                    created_at=created_at_val,
                    phone=phone,
                    email=email,
                    tax_number=tax_number,
                    balance_owing=balance_owing,
                )
            )
        return customers

    def read_customer_details_mapping(
        self,
        customer_ids: Sequence[int],
        entity_id: int,
        principal: Principal | None = None,
    ) -> dict[int, CustomerDetail]:
        """Fetch customer details and return mapping keyed by customer ID."""
        customers = self.read_customers(customer_ids, entity_id, principal)
        return {c.id: c for c in customers}
