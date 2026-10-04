"""SQL answer provenance: declared record routes plus validated link extraction.

The bundle carries each resource's record route and link grant; the functions
below are the one family that turns (resource, record id) into a same-origin
href, returning ``None`` on every miss. ``RECORD_LINK_REGISTRY`` serves only
trusted page-context records, which arrive keyed on Laravel table names.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from app.auth.principal import Principal
from app.business_query.definitions.member_resolution import primary_key_dimension
from app.business_query.definitions.schema import (
    DefinitionBundle,
    DimensionDefinition,
    ResourceBinding,
)
from app.business_query.outcomes import RecordRef
from app.models.schemas import PageRecord, RecordLink
from app.rag.provenance.safe_path import is_safe_same_origin_path

logger = logging.getLogger(__name__)

RECORD_LINK_REGISTRY: dict[str, str] = {
    "bills": "/bills/show/{id}",
    "work_orders": "/jobs/show/{id}",
    "customer_orders": "/customer-orders/{id}",
    "customers": "/customer/show/{id}",
}


def grants_satisfied(
    grants: Sequence[str],
    mode: Literal["any", "all"],
    principal: Principal,
) -> bool:
    """Whether the viewer holds the listed grants.

    Empty grants fail closed; mode ``any`` is an intersection, mode ``all`` a
    subset. No role bypass."""
    if not grants:
        return False
    held = set(principal.permissions)
    if mode == "all":
        return held.issuperset(grants)
    return any(grant in held for grant in grants)


def link_permissions_satisfied(binding: ResourceBinding, principal: Principal) -> bool:
    """Whether the viewer holds the route's link grant."""
    return grants_satisfied(binding.link_permissions, binding.link_permission_mode, principal)


def _resolvable_binding(
    bundle: DefinitionBundle, principal: Principal, resource: str
) -> ResourceBinding | None:
    """The resource's binding when it declares a route the viewer may open."""
    binding = next((r for r in bundle.resources if r.name == resource), None)
    if binding is None or not binding.record_route:
        return None
    if not link_permissions_satisfied(binding, principal):
        return None
    return binding


def resource_link_allowed(bundle: DefinitionBundle, principal: Principal, resource: str) -> bool:
    """Whether this bundle gives ``resource`` a route ``principal`` may open."""
    return _resolvable_binding(bundle, principal, resource) is not None


def record_href(
    bundle: DefinitionBundle, principal: Principal, resource: str, record_id: int
) -> str | None:
    """The same-origin path for one record, or None: no binding, no route, no
    grant, id <= 0, unsafe path, or a route that cannot carry the id."""
    binding = next((r for r in bundle.resources if r.name == resource), None)
    if binding is None or not binding.record_route:
        logger.debug("record link denied resource=%s reason=no_route", resource)
        return None
    if not link_permissions_satisfied(binding, principal):
        logger.debug("record link denied resource=%s reason=no_grant", resource)
        return None
    if record_id <= 0 or "{id}" not in binding.record_route:
        logger.debug("record link denied resource=%s reason=no_route", resource)
        return None
    url = binding.record_route.replace("{id}", str(record_id))
    if not is_safe_same_origin_path(url):
        logger.debug("record link denied resource=%s reason=unsafe", resource)
        return None
    return url


def record_href_template(
    bundle: DefinitionBundle, principal: Principal, resource: str, column_key: str
) -> str | None:
    """``record_href``'s template form: the route with ``{id}`` replaced by the
    row key that holds the id, or None on the same misses."""
    binding = _resolvable_binding(bundle, principal, resource)
    if binding is None or "{id}" not in binding.record_route:
        return None
    template = binding.record_route.replace("{id}", "{" + column_key + "}")
    if not is_safe_same_origin_path(template):
        return None
    return template


def _via_target_resource(bundle: DefinitionBundle, target_key: str) -> str | None:
    """The resource a ``link_via`` target points at: its declared ``link_key``,
    or its own resource when the target is that resource's primary key."""
    target = next((d for d in bundle.dimensions if d.name == target_key), None)
    if target is None:
        return None
    if target.link_key:
        return target.link_key
    if target.is_primary_key:
        return target.owning_resource
    return None


def column_link_resource(bundle: DefinitionBundle, column_key: str) -> str | None:
    """The one resource rule for a sealed column.

    The owning resource when the dimension is its resource's primary key; the
    declared ``link_key`` for an id foreign key; for a ``link_via`` look, the
    via target's resource. ``None`` otherwise — the column is not a record
    reference and renders as plain text."""
    dimension = next((d for d in bundle.dimensions if d.name == column_key), None)
    if dimension is None:
        return None
    if dimension.is_primary_key:
        return dimension.owning_resource
    if dimension.format == "id" and dimension.link_key:
        return dimension.link_key
    if dimension.link_via:
        return _via_target_resource(bundle, dimension.link_via)
    return None


def _look_label(
    bundle: DefinitionBundle, row: dict[str, Any], key: DimensionDefinition
) -> str | None:
    """The row value of the key's declared look, as text; None when absent."""
    if key.display_of is None:
        return None
    member = next(
        (
            entry.name
            for entry in bundle.capabilities
            if entry.kind == "dimension"
            and entry.resolves_to == key.display_of
            and entry.name in row
        ),
        key.display_of if key.display_of in row else None,
    )
    if member is None:
        return None
    value = row.get(member)
    return None if value is None else str(value)


def with_record_hrefs(
    refs: Sequence[RecordRef],
    rows: Sequence[dict[str, Any]],
    bundle: DefinitionBundle,
    principal: Principal,
) -> tuple[RecordRef, ...]:
    """Seal each record reference's href and human label once, under the viewer.

    The href comes from ``record_href``. The label keeps the receipt's own
    value first, then the row value of the resource key's declared look, then
    nothing. Labels are sealed here; a restored turn re-mints the href only.
    """
    sealed: list[RecordRef] = []
    for ref in refs:
        href = record_href(bundle, principal, ref.resource, ref.record_id)
        label = ref.label
        if label is None:
            in_range = ref.row_index is not None and 0 <= ref.row_index < len(rows)
            row = rows[ref.row_index] if in_range else None
            if row is not None:
                key = primary_key_dimension(bundle, ref.resource)
                if key is not None:
                    label = _look_label(bundle, row, key)
        sealed.append(ref.model_copy(update={"href": href, "label": label}))
    return tuple(sealed)


def is_safe_record_link_url(url: str) -> bool:
    return is_safe_same_origin_path(url)


def extract_record_links_from_page_records(
    records: list[PageRecord],
    cited_record_ids: list[str],
) -> list[RecordLink]:
    by_id = {record.id: record for record in records}
    trusted_ids = set(by_id)
    links: list[RecordLink] = []
    seen: set[str] = set()
    for raw_id in cited_record_ids:
        rid = str(raw_id)
        if rid in seen or rid not in trusted_ids:
            continue
        seen.add(rid)
        record = by_id[rid]
        template = RECORD_LINK_REGISTRY.get(record.link_key)
        if template is None:
            continue
        url = template.format(id=int(record.id))
        if not is_safe_same_origin_path(url):
            continue
        links.append(
            RecordLink(
                table=record.link_key,
                record_id=int(record.id),
                label=record.label,
                url=url,
            )
        )
    return links


def extract_record_links_from_record_refs(
    refs: Sequence[RecordRef],
) -> list[RecordLink]:
    """Mint links from a sealed Business Query ``record_refs`` sidecar.

    The seal mints ``href`` under the viewer's link grant; this copy step adds
    nothing. Fail closed (ADR 0022): a ref with no href, no human label, a
    non-positive id, or an unsafe path is omitted rather than guessed at."""
    links: list[RecordLink] = []
    seen: set[tuple[str, int]] = set()
    for ref in refs:
        if not ref.href or not ref.label:
            continue
        if ref.record_id <= 0:
            continue
        key = (ref.resource, ref.record_id)
        if key in seen:
            continue
        seen.add(key)
        if not is_safe_same_origin_path(ref.href):
            continue
        links.append(
            RecordLink(
                table=ref.resource,
                record_id=ref.record_id,
                label=ref.label,
                url=ref.href,
                preview=ref.preview,
            )
        )
    return links
