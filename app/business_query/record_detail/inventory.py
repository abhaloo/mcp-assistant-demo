"""Issued Materials, Inventory, and Artwork Detail Adapters.

Provides bounded, tenant-isolated detail reads with:
- Fail-closed permission gating for warehouse cost details and valuation
- Safe allowlisted artwork metadata strictly blocking file paths, S3 storage keys, and signed URLs

Also hosts the shared tenant/permission-gating helpers and datetime/date
parsers used across the record-detail package's SQL-backed adapters.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import urlparse

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from app.auth import Principal
from app.business_query.authorize.scoping import ScopeDenied
from app.business_query.record_detail.read_template import cap_ids

# Permission constants
PERMISSION_VIEW_JOB = "view job"
PERMISSION_VIEW_INVENTORY = "view inventory"
PERMISSION_VIEW_INVENTORY_COST = "view inventory cost"

# Roles that bypass tenant-boundary and permission checks entirely
# (general admin override -- used by _check_entity_and_permission).
_ADMIN_OVERRIDE_ROLES = frozenset({"superadmin", "admin"})

# Roles that may see inventory cost/valuation fields, in addition to holding
# PERMISSION_VIEW_INVENTORY_COST directly (used by InventoryDetailAdapter).
_COST_VISIBILITY_ROLES = frozenset({"superadmin", "finance", "admin"})


def _parse_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except ValueError:
            return None
    return None


def _parse_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        try:
            return date.fromisoformat(val)
        except ValueError:
            return None
    return None


def sanitize_artwork_filename(raw_value: str) -> str:
    """Extract safe display filename basename, stripping storage paths, S3 URIs,
    and query signatures.
    """
    if not raw_value:
        return ""

    cleaned = raw_value.strip()

    # Handle URLs (e.g., https://.../file.pdf?X-Amz-Signature=...)
    if "://" in cleaned:
        try:
            parsed = urlparse(cleaned)
            cleaned = parsed.path
        except Exception:
            cleaned = cleaned.split("://", 1)[-1]

    # Strip query parameters if still present
    if "?" in cleaned:
        cleaned = cleaned.split("?", 1)[0]

    # Strip fragments
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]

    # Strip Unix and Windows paths to basename
    name_posix = PurePosixPath(cleaned).name
    name_win = PureWindowsPath(name_posix).name
    return name_win


def _check_entity_and_permission(
    principal: Principal | None,
    *,
    entity_id: int,
    required_permissions: Sequence[str] | None = None,
    require_all: bool = False,
) -> None:
    """Verify tenant isolation and permissions (fail-closed)."""
    if principal is None:
        raise ScopeDenied()

    # Tenant boundary enforcement. A principal with no bound entity_id is
    # NOT exempt from this check -- it must fail the boundary the same way
    # a mismatched entity_id would, unless the principal is cross-entity or
    # holds an admin-override role.
    if principal.entity_id != entity_id:
        if not bool(principal.cross_entity) and principal.role not in (_ADMIN_OVERRIDE_ROLES):
            raise ScopeDenied()

    # Permission check
    if required_permissions:
        user_perms = set(principal.permissions or [])
        is_admin = principal.role in _ADMIN_OVERRIDE_ROLES
        if not is_admin:
            if require_all:
                if not all(p in user_perms for p in required_permissions):
                    raise ScopeDenied()
            else:
                if not any(p in user_perms for p in required_permissions):
                    raise ScopeDenied()


# --- Domain Models ---


class IssuedMaterialItem(BaseModel):
    """Actual warehouse consumption issued against a job."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    job_id: int
    work_order_id: int | None = None
    product_id: int
    product_name: str
    quantity: float
    unit: str | None = None
    source_type: Literal["issued"] = "issued"
    batch_number: str | None = None
    lot_number: str | None = None
    issued_at: datetime | None = None


class InventoryBatch(BaseModel):
    """Inventory batch / lot tracking metadata."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    inventory_id: int
    batch_number: str
    quantity: float
    expiry_date: date | None = None
    received_at: datetime | None = None


class InventoryMovement(BaseModel):
    """Warehouse stock movement record."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    inventory_id: int
    movement_type: str
    quantity: float
    reference_id: str | None = None
    created_at: datetime


class InventoryDetail(BaseModel):
    """Warehouse stock detail with permission-gated valuation."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    entity_id: int
    product_id: int
    product_name: str
    sku: str | None = None
    location: str | None = None
    quantity_on_hand: float
    quantity_reserved: float = 0.0
    quantity_available: float = 0.0
    unit_cost: float | None = None
    total_valuation: float | None = None
    currency: str | None = None
    batches: list[InventoryBatch] = Field(default_factory=list)
    movements: list[InventoryMovement] = Field(default_factory=list)


class ArtworkMetadata(BaseModel):
    """Safe allowlisted artwork metadata strictly blocking file paths and storage tokens."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    artwork_id: int
    job_id: int
    entity_id: int
    file_name: str
    status: str
    approved_at: datetime | None = None
    version: int | None = 1
    dimensions: str | None = None


# --- Adapters ---


class JobIssuedMaterialAdapter:
    """Reads warehouse consumption issued against jobs."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_issued_materials(
        self,
        job_id: int | None = None,
        work_order_id: int | None = None,
        *,
        entity_id: int,
        principal: Principal | None = None,
    ) -> list[IssuedMaterialItem]:
        """Read actual warehouse issued items for a job with tenant isolation."""
        _check_entity_and_permission(
            principal=principal,
            entity_id=entity_id,
            required_permissions=[PERMISSION_VIEW_JOB, PERMISSION_VIEW_INVENTORY],
        )

        if job_id is None and work_order_id is None:
            return []

        conditions = ["entity_id = :entity_id"]
        params: dict[str, Any] = {"entity_id": entity_id}

        if job_id is not None:
            conditions.append("job_id = :job_id")
            params["job_id"] = job_id
        if work_order_id is not None:
            conditions.append("work_order_id = :work_order_id")
            params["work_order_id"] = work_order_id

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT
                id,
                job_id,
                work_order_id,
                product_id,
                product_name,
                quantity,
                unit,
                batch_number,
                lot_number,
                issued_at
            FROM inventory_issues
            WHERE {where_clause}
            ORDER BY id ASC
        """

        with self._engine.connect() as conn:
            result = conn.execute(sa.text(sql), params)
            rows = result.mappings().fetchall()

        return [
            IssuedMaterialItem(
                id=int(r["id"]),
                job_id=int(r["job_id"]),
                work_order_id=(
                    int(r["work_order_id"]) if r.get("work_order_id") is not None else None
                ),
                product_id=int(r["product_id"]),
                product_name=str(r["product_name"]),
                quantity=float(r["quantity"]),
                unit=r.get("unit"),
                source_type="issued",
                batch_number=r.get("batch_number"),
                lot_number=r.get("lot_number"),
                issued_at=_parse_datetime(r.get("issued_at")),
            )
            for r in rows
        ]


class InventoryDetailAdapter:
    """Warehouse stock, batches, and movements reader with permission-gated cost."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_inventory(
        self,
        inventory_id: int,
        entity_id: int,
        principal: Principal | None = None,
    ) -> InventoryDetail | None:
        """Fetch a single inventory item."""
        results = self.read_inventory(
            inventory_ids=[inventory_id],
            entity_id=entity_id,
            principal=principal,
        )
        return results[0] if results else None

    def read_inventory(
        self,
        inventory_ids: Sequence[int],
        entity_id: int,
        principal: Principal | None = None,
    ) -> list[InventoryDetail]:
        """Read inventory items with related batches and movements."""
        _check_entity_and_permission(
            principal=principal,
            entity_id=entity_id,
            required_permissions=[PERMISSION_VIEW_INVENTORY],
        )

        if not inventory_ids:
            return []

        capped_ids = cap_ids(inventory_ids)
        if not capped_ids:
            return []

        # Check cost permission
        has_cost_perm = False
        if principal is not None:
            user_perms = set(principal.permissions or [])
            has_cost_perm = (
                PERMISSION_VIEW_INVENTORY_COST in user_perms
                or principal.role in _COST_VISIBILITY_ROLES
            )

        with self._engine.connect() as conn:
            # 1. Fetch inventories
            inv_stmt = sa.text(
                "SELECT id, entity_id, product_id, product_name, sku, location, "
                "quantity_on_hand, quantity_reserved, quantity_available, "
                "unit_cost, total_valuation, currency "
                "FROM inventories "
                "WHERE entity_id = :entity_id "
                "AND id IN :inventory_ids"
            ).bindparams(sa.bindparam("inventory_ids", expanding=True))

            inv_rows = (
                conn.execute(
                    inv_stmt,
                    {"entity_id": entity_id, "inventory_ids": tuple(capped_ids)},
                )
                .mappings()
                .fetchall()
            )

            if not inv_rows:
                return []

            matched_ids = [int(r["id"]) for r in inv_rows]

            # 2. Fetch batches
            batch_stmt = sa.text(
                "SELECT id, inventory_id, batch_number, quantity, expiry_date, received_at "
                "FROM inventory_batches "
                "WHERE inventory_id IN :inventory_ids "
                "ORDER BY id ASC"
            ).bindparams(sa.bindparam("inventory_ids", expanding=True))

            batch_rows = (
                conn.execute(
                    batch_stmt,
                    {"inventory_ids": tuple(matched_ids)},
                )
                .mappings()
                .fetchall()
            )

            batches_by_inv: dict[int, list[InventoryBatch]] = defaultdict(list)
            for b in batch_rows:
                batches_by_inv[int(b["inventory_id"])].append(
                    InventoryBatch(
                        id=int(b["id"]),
                        inventory_id=int(b["inventory_id"]),
                        batch_number=str(b["batch_number"]),
                        quantity=float(b["quantity"]),
                        expiry_date=_parse_date(b.get("expiry_date")),
                        received_at=_parse_datetime(b.get("received_at")),
                    )
                )

            # 3. Fetch movements
            mov_stmt = sa.text(
                "SELECT id, inventory_id, movement_type, quantity, reference_id, created_at "
                "FROM inventory_movements "
                "WHERE inventory_id IN :inventory_ids "
                "ORDER BY id ASC"
            ).bindparams(sa.bindparam("inventory_ids", expanding=True))

            mov_rows = (
                conn.execute(
                    mov_stmt,
                    {"inventory_ids": tuple(matched_ids)},
                )
                .mappings()
                .fetchall()
            )

            movs_by_inv: dict[int, list[InventoryMovement]] = defaultdict(list)
            for m in mov_rows:
                movs_by_inv[int(m["inventory_id"])].append(
                    InventoryMovement(
                        id=int(m["id"]),
                        inventory_id=int(m["inventory_id"]),
                        movement_type=str(m["movement_type"]),
                        quantity=float(m["quantity"]),
                        reference_id=m.get("reference_id"),
                        created_at=_parse_datetime(m["created_at"]) or datetime.now(UTC),
                    )
                )

        details: list[InventoryDetail] = []
        for r in inv_rows:
            inv_id = int(r["id"])
            details.append(
                InventoryDetail(
                    id=inv_id,
                    entity_id=int(r["entity_id"]),
                    product_id=int(r["product_id"]),
                    product_name=str(r["product_name"]),
                    sku=r.get("sku"),
                    location=r.get("location"),
                    quantity_on_hand=float(r["quantity_on_hand"]),
                    quantity_reserved=float(r.get("quantity_reserved") or 0.0),
                    quantity_available=float(r.get("quantity_available") or 0.0),
                    unit_cost=(
                        float(r["unit_cost"])
                        if (has_cost_perm and r.get("unit_cost") is not None)
                        else None
                    ),
                    total_valuation=(
                        float(r["total_valuation"])
                        if (has_cost_perm and r.get("total_valuation") is not None)
                        else None
                    ),
                    currency=(
                        str(r["currency"]) if (has_cost_perm and r.get("currency")) else None
                    ),
                    batches=batches_by_inv.get(inv_id, []),
                    movements=movs_by_inv.get(inv_id, []),
                )
            )

        return details

    def get_inventory_valuation(
        self,
        entity_id: int,
        inventory_ids: Sequence[int] | None = None,
        principal: Principal | None = None,
    ) -> dict[str, Any]:
        """Calculate total inventory valuation (strictly requires 'view inventory cost')."""
        _check_entity_and_permission(
            principal=principal,
            entity_id=entity_id,
            required_permissions=[PERMISSION_VIEW_INVENTORY, PERMISSION_VIEW_INVENTORY_COST],
            require_all=True,
        )

        conditions = ["entity_id = :entity_id"]
        params: dict[str, Any] = {"entity_id": entity_id}

        if inventory_ids:
            capped_ids = cap_ids(inventory_ids)
            if not capped_ids:
                return {"total_valuation": 0.0, "item_count": 0, "currency": "USD"}
            conditions.append("id IN :inventory_ids")
            params["inventory_ids"] = tuple(capped_ids)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT
                COUNT(id) as item_count,
                COALESCE(SUM(total_valuation), 0.0) as total_valuation,
                MAX(currency) as currency
            FROM inventories
            WHERE {where_clause}
        """

        stmt = sa.text(sql)
        if "inventory_ids" in params:
            stmt = stmt.bindparams(sa.bindparam("inventory_ids", expanding=True))

        with self._engine.connect() as conn:
            result = conn.execute(stmt, params).mappings().fetchone()

        if result is None:
            return {"total_valuation": 0.0, "item_count": 0, "currency": "USD"}

        return {
            "total_valuation": round(float(result["total_valuation"]), 2),
            "item_count": int(result["item_count"]),
            "currency": result["currency"] or "USD",
        }


class JobArtworkAdapter:
    """Reads safe allowlisted artwork metadata with absolute prohibition of storage tokens."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_artwork_metadata(
        self,
        job_id: int,
        entity_id: int,
        principal: Principal | None = None,
    ) -> list[ArtworkMetadata]:
        """Fetch allowlisted artwork metadata strictly omitting file paths and signed URLs."""
        _check_entity_and_permission(
            principal=principal,
            entity_id=entity_id,
            required_permissions=[PERMISSION_VIEW_JOB],
        )

        # Allowlisted columns only
        sql = """
            SELECT
                id,
                job_id,
                entity_id,
                file_name,
                status,
                approved_at,
                version,
                dimensions
            FROM job_artworks
            WHERE entity_id = :entity_id
            AND job_id = :job_id
            ORDER BY id ASC
        """

        with self._engine.connect() as conn:
            result = conn.execute(
                sa.text(sql),
                {"entity_id": entity_id, "job_id": job_id},
            )
            rows = result.mappings().fetchall()

        artworks: list[ArtworkMetadata] = []
        for r in rows:
            safe_name = sanitize_artwork_filename(str(r["file_name"]))
            artworks.append(
                ArtworkMetadata(
                    artwork_id=int(r["id"]),
                    job_id=int(r["job_id"]),
                    entity_id=int(r["entity_id"]),
                    file_name=safe_name,
                    status=str(r["status"]),
                    approved_at=_parse_datetime(r.get("approved_at")),
                    version=int(r["version"]) if r.get("version") is not None else 1,
                    dimensions=r.get("dimensions"),
                )
            )

        return artworks
