"""Trusted page-context policy compile and records-only prompt fencing.

Digestless contract: Laravel re-authorizes records on every ask; RAG trusts the
authenticated service JWT and bounded ``TrustedPageContext`` body only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models.page_context_v2 import PageContextV2
from app.models.schemas import TrustedPageContext

DispatchMode = Literal["records_only", "ambient_owner"]
OwnerBinding = Literal["none", "single_record"]


@dataclass(frozen=True)
class PageContextProfile:
    """Capabilities attached to one trusted producer profile.

    The route tuple is an identity allowlist; behavior comes from this
    metadata rather than from route-specific branches.  Ambient page context
    is deliberately not a dataset restriction: every currently registered
    profile can still reach a global structured Business Query.  A single
    owner identity is available only for a detail profile carrying exactly one
    authorized record.
    """

    version: int
    kind: str
    resource_type: str
    profile: str
    dispatch_mode: DispatchMode = "ambient_owner"
    owner_binding: OwnerBinding = "none"

    @property
    def allows_global_structured(self) -> bool:
        return self.dispatch_mode == "ambient_owner"


def _profile(
    version: int,
    kind: str,
    resource_type: str,
    profile: str,
) -> PageContextProfile:
    """Create a standard ambient profile from producer metadata.

    ``kind`` is the only capability input: only a single-record detail
    profile may expose an owner identity.  All other page shapes remain
    ambient navigation context and cannot silently become a records-only
    dataset.
    """

    return PageContextProfile(
        version=version,
        kind=kind,
        resource_type=resource_type,
        profile=profile,
        owner_binding="single_record" if kind == "detail" else "none",
    )


# Profile registry: (version, kind, resource_type, profile) → capabilities.
# Unknown combinations fail closed before model invocation.  Billing currently
# emits only these Jobs contracts; the registry stays explicit until each
# other resource has a producer-side authorization contract.
_POLICY_REGISTRY: dict[tuple[int, str, str, str], PageContextProfile] = {
    key: _profile(*key)
    for key in (
        (1, "list", "work_order", "jobs.index.v1"),
        (1, "detail", "work_order", "jobs.show.v1"),
        (1, "report", "work_order", "jobs.report.v1"),
        (1, "embedded_list", "work_order", "jobs.customer-list.v1"),
        (1, "embedded_list", "work_order", "jobs.bill-list.v1"),
        (1, "embedded_list", "work_order", "jobs.customer-order-list.v1"),
        (2, "detail", "work_order", "jobs.show.v2"),
        (2, "list", "work_order", "jobs.index.v2"),
        (2, "detail", "work_order", "jobs.show"),
        (2, "list", "work_order", "jobs.index"),
        (2, "report", "work_order", "jobs.report.v2"),
        (2, "embedded_list", "work_order", "jobs.customer-list.v2"),
        (2, "embedded_list", "work_order", "jobs.bill-list.v2"),
        (2, "embedded_list", "work_order", "jobs.customer-order-list.v2"),
    )
}


def registered_profile_keys() -> list[tuple[int, str, str, str]]:
    """Return sorted list of all profile keys registered in _POLICY_REGISTRY."""
    return sorted(_POLICY_REGISTRY.keys())


class UnknownPageContextProfileError(ValueError):
    """Trusted profile tuple is not registered — fail closed."""


@dataclass(frozen=True)
class PageContextPolicy:
    """Immutable server-only policy compiled from trusted context identity."""

    version: int
    kind: str
    resource_type: str
    profile: str
    dispatch_mode: DispatchMode
    owner_ids: tuple[str, ...] = ()
    owner_id: str | None = None
    definition_bundle_hash: str | None = None
    compatibility_epoch: int | None = None

    @property
    def records_only(self) -> bool:
        return self.dispatch_mode == "records_only"

    @property
    def allows_global_structured(self) -> bool:
        """Whether this profile may reach global structured query planning."""
        return not self.records_only


def extract_authorized_owner_ids(
    page_context: TrustedPageContext | PageContextV2,
) -> list[str]:
    """Extract authorized owner IDs without requiring or decoding detail values."""
    return [r.id for r in page_context.records]


def compile_page_context_policy(
    page_context: TrustedPageContext | PageContextV2,
) -> PageContextPolicy:
    """Compile policy from trusted (version, kind, resource_type, profile) only."""
    key = (
        page_context.version,
        page_context.kind,
        page_context.resource_type,
        page_context.profile,
    )
    profile = _POLICY_REGISTRY.get(key)
    if profile is None:
        raise UnknownPageContextProfileError(f"unknown page context profile: {key}")
    for record in page_context.records:
        record_resource_type = getattr(record, "resource_type", None)
        if record_resource_type is not None and record_resource_type != page_context.resource_type:
            raise UnknownPageContextProfileError(
                f"page record resource type does not match profile: {key}"
            )
    owner_ids = tuple(extract_authorized_owner_ids(page_context))
    owner_id = (
        owner_ids[0] if profile.owner_binding == "single_record" and len(owner_ids) == 1 else None
    )
    definition_bundle_hash = getattr(page_context, "definition_bundle_hash", None)
    compatibility_epoch = getattr(page_context, "compatibility_epoch", None)
    return PageContextPolicy(
        version=page_context.version,
        kind=page_context.kind,
        resource_type=page_context.resource_type,
        profile=page_context.profile,
        dispatch_mode=profile.dispatch_mode,
        owner_ids=owner_ids,
        owner_id=owner_id,
        definition_bundle_hash=definition_bundle_hash,
        compatibility_epoch=compatibility_epoch,
    )


_FENCE_COLLISION_RE = re.compile(r"BEGIN PAGE RECORDS|END PAGE RECORDS", re.IGNORECASE)


def format_records_fence(page_context: TrustedPageContext, *, nonce: str | None = None) -> str:
    """Nonce-fenced compact JSON of trusted records for the records-only prompt."""
    from app.rag.prompt_fence import fenced_json_block

    payload = {
        "title": page_context.title,
        "profile": page_context.profile,
        "records": [r.model_dump(mode="json") for r in page_context.records],
    }
    return fenced_json_block(
        marker="PAGE RECORDS",
        payload=payload,
        pattern=_FENCE_COLLISION_RE,
        nonce=nonce,
    )
