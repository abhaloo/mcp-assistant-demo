"""Rows for the AI page-context surface: profile fields resolved through the billing export."""

from __future__ import annotations

from app.coverage.billing_export import BillingExport
from app.coverage.model import (
    Binding,
    ComputedBinding,
    SurfaceRow,
    UnresolvedBinding,
)
from app.coverage.schema_inventory import SchemaInventory
from app.coverage.surfaces._chain import resolve_chain

RESOURCE_TYPE_RESOURCE = {"work_order": "job"}


def page_context_rows(
    export: BillingExport,
    registry: list[tuple[int, str, str, str]],
    inventory: SchemaInventory,
) -> list[SurfaceRow]:
    """Emit SurfaceRow records for registered page-context profiles against billing export."""
    models = export.model_by_class()
    export_profiles = {p.profile: p for p in export.page_context_profiles}

    registered_profile_map: dict[str, str] = {}
    for _version, _kind, resource_type, profile_name in registry:
        registered_profile_map[profile_name] = resource_type

    rows: list[SurfaceRow] = []

    # Emits UnresolvedBinding for registered profiles missing from the export
    for prof_name, res_type in sorted(registered_profile_map.items()):
        if prof_name not in export_profiles:
            rows.append(
                SurfaceRow(
                    surface="page_context",
                    resource=RESOURCE_TYPE_RESOURCE.get(res_type, res_type),
                    field="*",
                    member=f"page_context:{prof_name}",
                    view=prof_name,
                    bindings=[UnresolvedBinding(reason="missing_profile_in_export")],
                    permissions=[],
                    evidence=f"page_context profile {prof_name}",
                )
            )

    # Process profiles present in the export
    for profile in export.page_context_profiles:
        is_known = profile.profile in registered_profile_map
        resource = RESOURCE_TYPE_RESOURCE.get(profile.resource_type, profile.resource_type)
        for field, chain in profile.fields.items():
            if not is_known:
                bindings: list[Binding] = [UnresolvedBinding(reason="registry_mismatch")]
            elif chain is None:
                bindings = [
                    ComputedBinding(expression=f"{profile.class_name} resolver computes {field}")
                ]
            else:
                bindings = resolve_chain(profile.root_model, chain, False, models, inventory)
            rows.append(
                SurfaceRow(
                    surface="page_context",
                    resource=resource,
                    field=field,
                    member=f"page_context:{profile.profile}.{field}",
                    view=profile.profile,
                    bindings=bindings,
                    permissions=[],
                    evidence=f"page_context profile {profile.profile}",
                )
            )

    return rows
