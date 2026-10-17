"""A replayed answer's stored tables are disclosed again only after the person's
current grants are checked against everything they show, the way a fresh turn
authorizes."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from app.auth.principal import Principal
from app.business_query.authorize.capability import visible_members
from app.business_query.definitions import DefinitionBundle
from app.business_query.definitions.loader import bundle_for_manifest
from app.business_query.outcomes import UnifiedResultEnvelope
from app.business_query.ports import BusinessProgressSink
from app.business_query.wire.module_scoping import load_bundle
from app.core.errors import ContinuationUnavailableError
from app.models.ask_response import Answer
from app.services.ask_result_projection import result_envelopes
from app.services.ask_v2_result import V2Result

logger = logging.getLogger(__name__)

_COMPARISON_SUFFIXES = ("__delta_pct", "__previous", "__delta")


def _keys(envelope: UnifiedResultEnvelope) -> Iterator[str]:
    yield from (column.key for column in envelope.columns)
    for row in envelope.rows:
        yield from row
    for found in (*envelope.records, *envelope.aggregates):
        yield from found.fields


def _families(envelope: UnifiedResultEnvelope) -> Iterator[str]:
    yield from (detail.family for detail in envelope.record_details)
    for record in envelope.records:
        yield from (detail.family for detail in record.details)


def _measure(key: str) -> str:
    for suffix in _COMPARISON_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _resource(key: str) -> str:
    return key.split(".", 1)[0]


def _first_rejected(
    envelope: UnifiedResultEnvelope, principal: Principal, bundle: DefinitionBundle
) -> str | None:
    if not principal.manifest_hash or envelope.manifest_hash != principal.manifest_hash:
        return "manifest_hash"
    named = {entry.name for entry in bundle.capabilities}
    visible = visible_members(principal, bundle)
    visible_resources = {_resource(name) for name in visible}
    for key in _keys(envelope):
        base = _measure(key)
        if base in named:
            if base not in visible:
                return key
        elif _resource(base) not in visible_resources:
            return key
    for family in _families(envelope):
        if family not in visible:
            return family
    return None


def replay_may_disclose(answer: Answer, principal: Principal, bundle: DefinitionBundle) -> bool:
    """True when every stored table, row and detail is still one this person may see."""
    for envelope in result_envelopes(answer):
        rejected = _first_rejected(envelope, principal, bundle)
        if rejected is not None:
            logger.info("replay re-authorization rejected name=%s", rejected)
            return False
    return True


def reauthorize_replay(
    result: V2Result,
    principal: Principal,
    *,
    correlation_id: str,
    progress: BusinessProgressSink | None,
) -> None:
    """Open the table gate for a replay whose stored tables still pass; fail closed otherwise."""
    if not isinstance(result, Answer) or not result_envelopes(result):
        return
    bundle = load_bundle(bundle_for_manifest, principal, correlation_id)
    if not isinstance(bundle, DefinitionBundle) or not replay_may_disclose(
        result, principal, bundle
    ):
        raise ContinuationUnavailableError("continuation_unavailable")
    if progress is not None:
        progress.emit("authorize")
        progress.emit("authorized")
