"""Product and Supplier Catalog Detail Adapters.

Tenant-isolated, permission-gated reads over the Product and Supplier
catalog resources. Split out from `record_detail.inventory` to keep that
module under the project's per-file line ceiling; it reuses inventory's
shared tenant/permission-gating helper and datetime parser rather than
duplicating them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from app.auth import Principal
from app.business_query.record_detail.inventory import (
    _check_entity_and_permission,
    _parse_datetime,
)
from app.business_query.record_detail.read_template import cap_ids

PERMISSION_VIEW_PRODUCT = "view product"
PERMISSION_VIEW_SUPPLIER = "view supplier"


class ProductDetail(BaseModel):
    """Product catalog definition and pricing."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    entity_id: int
    name: str
    sku: str | None = None
    category: str | None = None
    unit: str | None = None
    is_active: bool = True
    unit_price: float | None = None
    unit_cost: float | None = None
    created_at: datetime


class SupplierDetail(BaseModel):
    """Supplier catalog definition and terms."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    entity_id: int
    name: str
    code: str | None = None
    status: str | None = None
    payment_terms: str | None = None
    contact_name: str | None = None
    is_active: bool = True
    created_at: datetime


class ProductDetailAdapter:
    """Tenant-isolated Product detail and catalog reader."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_product(
        self,
        product_id: int,
        entity_id: int,
        principal: Principal | None = None,
    ) -> ProductDetail | None:
        """Fetch a single product by ID."""
        results = self.read_products(
            product_ids=[product_id],
            entity_id=entity_id,
            principal=principal,
        )
        return results[0] if results else None

    def read_products(
        self,
        product_ids: Sequence[int],
        entity_id: int,
        principal: Principal | None = None,
    ) -> list[ProductDetail]:
        """Read products with tenant isolation and 'view product' permission."""
        _check_entity_and_permission(
            principal=principal,
            entity_id=entity_id,
            required_permissions=[PERMISSION_VIEW_PRODUCT],
        )

        if not product_ids:
            return []

        capped_ids = cap_ids(product_ids)
        if not capped_ids:
            return []

        stmt = sa.text(
            "SELECT id, entity_id, name, sku, category, unit, is_active, "
            "unit_price, unit_cost, created_at "
            "FROM products "
            "WHERE entity_id = :entity_id "
            "AND id IN :product_ids "
            "ORDER BY id ASC"
        ).bindparams(sa.bindparam("product_ids", expanding=True))

        with self._engine.connect() as conn:
            result = conn.execute(
                stmt,
                {"entity_id": entity_id, "product_ids": tuple(capped_ids)},
            )
            rows = result.mappings().fetchall()

        return [
            ProductDetail(
                id=int(r["id"]),
                entity_id=int(r["entity_id"]),
                name=str(r["name"]),
                sku=r.get("sku"),
                category=r.get("category"),
                unit=r.get("unit"),
                is_active=bool(r.get("is_active", True)),
                unit_price=float(r["unit_price"]) if r.get("unit_price") is not None else None,
                unit_cost=float(r["unit_cost"]) if r.get("unit_cost") is not None else None,
                created_at=_parse_datetime(r["created_at"]) or datetime.now(UTC),
            )
            for r in rows
        ]


class SupplierDetailAdapter:
    """Tenant-isolated Supplier detail and catalog reader."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_supplier(
        self,
        supplier_id: int,
        entity_id: int,
        principal: Principal | None = None,
    ) -> SupplierDetail | None:
        """Fetch a single supplier by ID."""
        results = self.read_suppliers(
            supplier_ids=[supplier_id],
            entity_id=entity_id,
            principal=principal,
        )
        return results[0] if results else None

    def read_suppliers(
        self,
        supplier_ids: Sequence[int],
        entity_id: int,
        principal: Principal | None = None,
    ) -> list[SupplierDetail]:
        """Read suppliers with tenant isolation and 'view supplier' permission."""
        _check_entity_and_permission(
            principal=principal,
            entity_id=entity_id,
            required_permissions=[PERMISSION_VIEW_SUPPLIER],
        )

        if not supplier_ids:
            return []

        capped_ids = cap_ids(supplier_ids)
        if not capped_ids:
            return []

        stmt = sa.text(
            "SELECT id, entity_id, name, code, status, payment_terms, "
            "contact_name, is_active, created_at "
            "FROM suppliers "
            "WHERE entity_id = :entity_id "
            "AND id IN :supplier_ids "
            "ORDER BY id ASC"
        ).bindparams(sa.bindparam("supplier_ids", expanding=True))

        with self._engine.connect() as conn:
            result = conn.execute(
                stmt,
                {"entity_id": entity_id, "supplier_ids": tuple(capped_ids)},
            )
            rows = result.mappings().fetchall()

        return [
            SupplierDetail(
                id=int(r["id"]),
                entity_id=int(r["entity_id"]),
                name=str(r["name"]),
                code=r.get("code"),
                status=r.get("status"),
                payment_terms=r.get("payment_terms"),
                contact_name=r.get("contact_name"),
                is_active=bool(r.get("is_active", True)),
                created_at=_parse_datetime(r["created_at"]) or datetime.now(UTC),
            )
            for r in rows
        ]
