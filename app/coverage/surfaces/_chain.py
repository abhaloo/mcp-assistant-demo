"""Resolve an attribute chain against exported models and the live schema inventory."""

from __future__ import annotations

from app.coverage.billing_export import ExportModel
from app.coverage.model import (
    Binding,
    ColumnBinding,
    ComputedBinding,
    UnresolvedBinding,
)
from app.coverage.schema_inventory import SchemaInventory


def _short(class_name: str) -> str:
    """Return the class name without its namespace prefix."""
    return class_name.rsplit("\\", 1)[-1]


def resolve_chain(
    model: ExportModel | str,
    chain: list[str],
    is_method: bool,
    models: dict[str, ExportModel],
    inventory: SchemaInventory,
) -> list[Binding]:
    """Bind an attribute chain to a base column, a computed expression, or a reason.

    Each hop before the last must be an exported relation with status "ok". The last
    element binds to a base column when the inventory holds it, else to an accessor,
    an appended attribute, or a relation. Every other outcome returns one
    UnresolvedBinding that names the hop that stopped the walk.
    """
    if isinstance(model, str):
        current_model = models.get(model)
        if current_model is None:
            return [UnresolvedBinding(reason=f"model_not_exported:{model}")]
    else:
        current_model = model

    if not chain:
        return [UnresolvedBinding(reason="empty_chain")]

    for hop in chain[:-1]:
        relation = next((r for r in current_model.relations if r.name == hop), None)
        if relation is None or relation.status != "ok" or relation.related_model is None:
            return [UnresolvedBinding(reason=f"relation_not_exported:{hop}")]
        next_model = models.get(relation.related_model)
        if next_model is None:
            return [UnresolvedBinding(reason=f"model_not_exported:{relation.related_model}")]
        current_model = next_model

    last = chain[-1]
    if is_method:
        return [ComputedBinding(expression=f"{_short(current_model.class_name)}::{last}()")]

    rel = next((r for r in current_model.relations if r.name == last), None)
    if rel is not None and (rel.status != "ok" or rel.related_model is None):
        return [UnresolvedBinding(reason=f"relation_not_exported:{last}")]

    columns = {c.column for c in inventory.tables.get(current_model.table, [])}
    if last in columns:
        return [ColumnBinding(table=current_model.table, column=last)]

    if last in current_model.accessors or last in current_model.appends:
        return [ComputedBinding(expression=f"{_short(current_model.class_name)}::{last} accessor")]

    if rel is not None and rel.status == "ok":
        return [ComputedBinding(expression=f"{_short(current_model.class_name)}::{last} relation")]

    return [UnresolvedBinding(reason=f"cannot_resolve_{last}")]
