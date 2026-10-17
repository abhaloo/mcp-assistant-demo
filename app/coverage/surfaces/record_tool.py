"""Rows for the MCP record tool: manifest readable fields bound through projection views."""

from __future__ import annotations

from app.coverage.model import SurfaceRow
from app.coverage.schema_inventory import SchemaInventory
from app.coverage.view_lineage import ViewLineage, resolve_projection_column
from app.policy.manifest_loader import Manifest


def record_tool_rows(
    manifest: Manifest,
    lineage: ViewLineage,
    inventory: SchemaInventory,
) -> list[SurfaceRow]:
    """Generate SurfaceRow entries for all readable fields in the policy manifest."""
    if not manifest.resources:
        raise ValueError("manifest resources must not be empty")

    rows: list[SurfaceRow] = []
    for name, resource in manifest.resources.items():
        view = resource.projection_name
        for field in resource.readable_fields:
            rows.append(
                SurfaceRow(
                    surface="record_tool",
                    resource=name,
                    field=field,
                    member=f"field:{name}.{field}",
                    view=view if view in inventory.views else None,
                    bindings=resolve_projection_column(view, field, lineage, inventory),
                    permissions=list(resource.read_permissions),
                    evidence=f"manifest readable_fields via {view}",
                )
            )
    return rows
