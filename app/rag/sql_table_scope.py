"""Declared per-table row-scope columns for the SQL agent (S1).

Every table in ``TIER_TABLES`` must appear here. Kind ``scoped`` requires an
entity column bind for non–``cross_entity`` principals; ``global`` is unscoped
reference data (join-leak tests still cover bridges into scoped tables).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class UnknownTableScope(LookupError):
    """Raised when a SQL table has no scope declaration."""


class TableScopeDecl(BaseModel):
    kind: Literal["scoped", "global"]
    entity: str | None = None
    department: str | None = None

    @model_validator(mode="after")
    def _kind_matches_columns(self) -> TableScopeDecl:
        if self.kind == "scoped" and not self.entity:
            raise ValueError("scoped tables must declare an entity column")
        if self.kind == "global" and (self.entity is not None or self.department is not None):
            raise ValueError("global tables must not declare scope columns")
        return self


def _scoped(entity: str = "entity_id") -> TableScopeDecl:
    return TableScopeDecl(kind="scoped", entity=entity)


def _global() -> TableScopeDecl:
    return TableScopeDecl(kind="global")


# D3(a): explicit globals + join-leak coverage. Operational ``all``-tier tables
# work_orders / work_order_items are scoped (fail-closed); ware_houses is global
# reference inventory location data.
_TABLE_SCOPE: dict[str, TableScopeDecl] = {
    # global / reference
    "products": _global(),
    "categories": _global(),
    "units": _global(),
    "departments": _global(),
    "ware_houses": _global(),
    # scoped — finance
    "bills": _scoped(),
    "bill_items": _scoped(),
    "journals": _scoped(),
    "expenses": _scoped(),
    "accounts": _scoped(),
    "credit_notes": _scoped(),
    "suppliers": _scoped(),
    # scoped — customers / sales
    "customers": _scoped(),
    "customer_orders": _scoped(),
    "customer_order_histories": _scoped(),
    # scoped — warehouse
    "inventories": _scoped(),
    "inventory_items": _scoped(),
    # scoped — jobs (entity_id; not silently global)
    "work_orders": _scoped(),
    "work_order_items": _scoped(),
}


def get_table_scope(table: str) -> TableScopeDecl:
    """Return the scope declaration for ``table`` (case-insensitive)."""
    key = table.strip().lower()
    try:
        return _TABLE_SCOPE[key]
    except KeyError as exc:
        raise UnknownTableScope(table) from exc
