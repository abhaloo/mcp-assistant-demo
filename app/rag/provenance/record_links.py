"""SQL answer provenance: whitelist route registry + validated record-link extraction."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from types import MappingProxyType

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

# Business Query Record Ref resource name -> RECORD_LINK_REGISTRY table (ADR 0053
# Decision 5). Deliberately separate from SQL TIER_TABLES / get_allowed_tables —
# refs are sealed by the Module, not derived from a caller's access tiers.
_RECORD_REF_RESOURCE_TABLES: dict[str, str] = {
    "customer": "customers",
    "invoice": "bills",
    "job": "work_orders",
    "customer_order": "customer_orders",
}

_COLUMN_ENTITY_TO_REGISTRY: MappingProxyType[str, str] = MappingProxyType(
    {**_RECORD_REF_RESOURCE_TABLES, "bill": "bills"}
)


def _entity_from_column_key(key: str) -> str:
    if "." in key:
        return key.split(".", 1)[0].casefold()
    lower = key.casefold()
    if lower.endswith("_id"):
        return lower[:-3]
    return lower


def identifier_href_template(column_key: str) -> str | None:
    """Return a route template for an identifier column key, or None when unmapped."""
    entity = _entity_from_column_key(column_key)
    table = _COLUMN_ENTITY_TO_REGISTRY.get(entity)
    if table is None:
        return None
    template = RECORD_LINK_REGISTRY.get(table)
    if template is None:
        return None
    return template.replace("{id}", "{" + column_key + "}")


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
    """Mint links from a sealed Business Query ``record_refs`` sidecar (ADR 0053).

    Never from ``Answered.rows`` or SQL strings. Fail closed (ADR 0022): a ref
    with no human label, a non-positive id, or an unrecognized ``resource`` is
    omitted rather than guessed at.
    """
    links: list[RecordLink] = []
    seen: set[tuple[str, int]] = set()
    for ref in refs:
        if not ref.label:
            continue
        if ref.record_id <= 0:
            continue
        table = _RECORD_REF_RESOURCE_TABLES.get(ref.resource)
        if table is None:
            continue
        template = RECORD_LINK_REGISTRY.get(table)
        if template is None:
            continue
        key = (table, ref.record_id)
        if key in seen:
            continue
        seen.add(key)
        url = template.format(id=ref.record_id)
        if not is_safe_same_origin_path(url):
            continue
        links.append(
            RecordLink(
                table=table,
                record_id=ref.record_id,
                label=ref.label,
                url=url,
                preview=ref.preview,
            )
        )
    return links
