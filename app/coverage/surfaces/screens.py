"""Rows for detail screens: Blade attribute chains resolved through exported model relations."""

from __future__ import annotations

from pathlib import PurePosixPath

from app.coverage.billing_export import BillingExport, ExportBlade, ExportModel
from app.coverage.model import SurfaceRow, UnresolvedBinding
from app.coverage.schema_inventory import SchemaInventory
from app.coverage.surfaces._chain import resolve_chain

ROUTE_RESOURCE: dict[str, str] = {
    "work-order.show": "job",
    "customer-orders.show": "customer_order",
    "customer.show": "customer",
    "bill.show": "invoice",
    "supplier.show": "supplier",
    "inventory.show": "inventory",
}


def _blade_rows(
    blade: ExportBlade,
    resource: str,
    route_perms: list[str],
    models: dict[str, ExportModel],
    inventory: SchemaInventory,
    route_view_variables: dict[str, str] | None = None,
) -> list[SurfaceRow]:
    aliases = {a.alias: (a.variable, a.chain) for a in blade.loop_aliases}
    view_vars = {**(route_view_variables or {}), **blade.view_variables}
    rows: list[SurfaceRow] = []
    for ref in blade.refs:
        variable, chain = ref.variable, list(ref.chain)
        seen = set()
        while variable in aliases and variable not in seen:
            seen.add(variable)
            source_var, source_chain = aliases[variable]
            variable, chain = source_var, [*source_chain, *chain]
        if variable != blade.root_variable:
            if variable in view_vars:
                target_model = view_vars[variable]
                bindings = resolve_chain(
                    target_model,
                    chain,
                    ref.is_method,
                    models,
                    inventory,
                )
            else:
                bindings = [UnresolvedBinding(reason=f"root_variable_unbound:{variable}")]
        else:
            bindings = resolve_chain(
                blade.root_model,
                chain,
                ref.is_method,
                models,
                inventory,
            )
        field = ".".join(chain if variable == blade.root_variable else [variable, *chain])
        rows.append(
            SurfaceRow(
                surface="screen",
                resource=resource,
                field=field,
                member=f"screen:{blade.file}:{ref.line}",
                view=PurePosixPath(blade.file).stem.removesuffix(".blade"),
                bindings=bindings,
                permissions=sorted({*route_perms, *blade.can_gates}),
                evidence=f"{blade.file}:{ref.line} ${ref.variable}->{'->'.join(ref.chain)}",
            )
        )
    return rows


def screen_rows(export: BillingExport, inventory: SchemaInventory) -> list[SurfaceRow]:
    models = export.model_by_class()
    blades = {b.file: b for b in export.blades}
    rows: list[SurfaceRow] = []
    for route in export.routes:
        resource = ROUTE_RESOURCE.get(route.name, route.name)
        route_perms = [
            m.removeprefix("permission:") for m in route.middleware if m.startswith("permission:")
        ]
        for file in route.blades:
            blade = blades.get(file)
            if blade is None:
                rows.append(
                    SurfaceRow(
                        surface="screen",
                        resource=resource,
                        field="*",
                        member=f"screen:{file}",
                        view=PurePosixPath(file).stem.removesuffix(".blade"),
                        bindings=[UnresolvedBinding(reason=f"blade_not_exported:{file}")],
                        permissions=route_perms,
                        evidence=f"route {route.name} blade {file}",
                    )
                )
                continue
            rows.extend(
                _blade_rows(
                    blade,
                    resource,
                    route_perms,
                    models,
                    inventory,
                    route_view_variables=route.view_variables,
                )
            )
    return rows
