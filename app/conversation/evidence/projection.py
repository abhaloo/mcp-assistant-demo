"""Restore-time projection of a sealed business query for the restoring viewer.

The stored snapshot stays byte-stable: tables and record links are computed
here, per principal, so a revoked link grant drops the link without touching
the sealed data."""

from __future__ import annotations

from collections.abc import Callable

from app.auth.principal import Principal
from app.business_query.definitions.loader import bundle_for_declared_hash
from app.business_query.definitions.schema import DefinitionBundle
from app.business_query.outcomes import UnifiedResultEnvelope
from app.conversation.evidence.contracts import RestoredTable, RestoredTurn
from app.models.ask_v2_events import TableColumn
from app.models.schemas import RecordLink
from app.rag.provenance.record_links import (
    column_link_resource,
    extract_record_links_from_record_refs,
    record_href,
    resource_link_allowed,
)
from app.services.ask_result_projection import envelope_presentation, extract_table_data


def project_for_viewer(
    turn: RestoredTurn,
    principal: Principal,
    *,
    bundle_resolver: Callable[[str], DefinitionBundle] = bundle_for_declared_hash,
) -> RestoredTurn:
    """Rebuild the live paint's tables and record links for the restoring viewer.

    The projection is the same one the stream used, re-read at this moment: a
    sealed column keeps the template the seal minted except when the restoring
    viewer no longer holds that route's link grant; restore never mints a
    template. Sealed record refs mint ``href`` only and keep the sealed
    ``label``. Nothing here is stored."""
    wire = turn.business_query
    envelopes: list[UnifiedResultEnvelope] = []
    if wire is not None:
        envelopes = list(wire.envelopes) or ([wire.envelope] if wire.envelope is not None else [])
    if not envelopes:
        return turn
    bundles: dict[str, DefinitionBundle] = {}
    tables: list[RestoredTable] = []
    links: list[RecordLink] = []
    for ordinal, envelope in enumerate(envelopes):
        bundle = _bundle_for(bundles, bundle_resolver, envelope)
        tables.append(_restored_table(turn.run_id, ordinal, envelope, bundle, principal))
        links.extend(_minted_links(envelope, bundle, principal))
    return turn.model_copy(update={"tables": tuple(tables), "record_links": tuple(links)})


def _bundle_for(
    bundles: dict[str, DefinitionBundle],
    resolver: Callable[[str], DefinitionBundle],
    envelope: UnifiedResultEnvelope,
) -> DefinitionBundle:
    declared = envelope.bundle_hash
    if declared not in bundles:
        bundles[declared] = resolver(declared)
    return bundles[declared]


def _restored_table(
    run_id: str,
    ordinal: int,
    envelope: UnifiedResultEnvelope,
    bundle: DefinitionBundle,
    principal: Principal,
) -> RestoredTable:
    rows, columns, _ = extract_table_data(envelope)
    columns = [_relinked(column, bundle, principal) for column in columns]
    return RestoredTable(
        table_id=f"table-{run_id}-{ordinal}",
        columns=columns,
        rows=rows,
        returned_row_count=envelope.returned_row_count,
        total_row_count=envelope.total_row_count,
        presentation=envelope_presentation(envelope),
    )


def _relinked(column: TableColumn, bundle: DefinitionBundle, principal: Principal) -> TableColumn:
    """The sealed template narrows, never mints: keep it only while the
    restoring viewer holds each column route's link grant; strip otherwise."""
    if column.href_template is None:
        return column
    resource = column_link_resource(bundle, column.key)
    if resource is None or not resource_link_allowed(bundle, principal, resource):
        return column.model_copy(update={"href_template": None})
    return column


def _minted_links(
    envelope: UnifiedResultEnvelope, bundle: DefinitionBundle | None, principal: Principal
) -> list[RecordLink]:
    if bundle is None:
        return []
    minted = [
        ref.model_copy(update={"href": record_href(bundle, principal, ref.resource, ref.record_id)})
        for ref in envelope.record_refs
    ]
    return extract_record_links_from_record_refs(minted)
