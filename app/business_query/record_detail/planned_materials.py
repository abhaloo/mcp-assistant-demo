"""Job Planned Material Detail Adapter.

Reads estimated/ticketed material lines from work orders, kept strictly
separate from actual warehouse consumption (see `record_detail.inventory`).
"""

from __future__ import annotations

from typing import Any, Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from app.auth import Principal
from app.business_query.record_detail.inventory import (
    PERMISSION_VIEW_JOB,
    _check_entity_and_permission,
)


class PlannedMaterialItem(BaseModel):
    """Estimated/ticketed material line from work orders."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    id: int
    work_order_id: int
    job_id: int | None = None
    product_id: int
    product_name: str
    quantity: float
    unit: str | None = None
    source_type: Literal["planned"] = "planned"
    notes: str | None = None


class JobPlannedMaterialAdapter:
    """Reads estimated/ticketed materials from work orders."""

    def __init__(self, engine: sa.engine.Engine) -> None:
        self._engine = engine

    def get_planned_materials(
        self,
        job_id: int | None = None,
        work_order_id: int | None = None,
        *,
        entity_id: int,
        principal: Principal | None = None,
    ) -> list[PlannedMaterialItem]:
        """Read planned material lines for a job or work order with tenant isolation."""
        _check_entity_and_permission(
            principal=principal,
            entity_id=entity_id,
            required_permissions=[PERMISSION_VIEW_JOB],
        )

        if job_id is None and work_order_id is None:
            return []

        conditions = ["wo.entity_id = :entity_id"]
        params: dict[str, Any] = {"entity_id": entity_id}

        if job_id is not None:
            conditions.append("wo.job_id = :job_id")
            params["job_id"] = job_id
        if work_order_id is not None:
            conditions.append("wo.id = :work_order_id")
            params["work_order_id"] = work_order_id

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT
                woi.id,
                woi.work_order_id,
                wo.job_id,
                woi.product_id,
                woi.product_name,
                woi.quantity,
                woi.unit,
                woi.notes
            FROM work_order_items woi
            JOIN work_orders wo ON woi.work_order_id = wo.id
            WHERE {where_clause}
            ORDER BY woi.id ASC
        """

        with self._engine.connect() as conn:
            result = conn.execute(sa.text(sql), params)
            rows = result.mappings().fetchall()

        return [
            PlannedMaterialItem(
                id=int(r["id"]),
                work_order_id=int(r["work_order_id"]),
                job_id=int(r["job_id"]) if r.get("job_id") is not None else None,
                product_id=int(r["product_id"]),
                product_name=str(r["product_name"]),
                quantity=float(r["quantity"]),
                unit=r.get("unit"),
                source_type="planned",
                notes=r.get("notes"),
            )
            for r in rows
        ]
