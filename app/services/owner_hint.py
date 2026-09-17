"""Translate trusted detail-page identity into a Business Query owner hint."""

from __future__ import annotations

from app.business_query.wire.module import BusinessQueryOwnerHint
from app.conversation.turn import TurnContext
from app.rag.page_context import UnknownPageContextProfileError

# ``work_order`` is the historical Jobs page wire label.  Business Query's
# canonical resource is ``job``.  Future page producers should send their
# canonical resource name directly; this compatibility map is deliberately
# independent of question wording and detail families.
_RESOURCE_TYPE_ALIASES: dict[str, str] = {"work_order": "job"}


def canonical_resource_type(resource_type: str) -> str:
    """Return the stable Business Query resource name for a page label."""

    return _RESOURCE_TYPE_ALIASES.get(resource_type, resource_type)


def owner_hint_for_context(ctx: TurnContext) -> BusinessQueryOwnerHint | None:
    """Build the one authorized owner hint eligible for this turn.

    A single explicit Global Search record is itself a trusted owner binding
    and always wins over ambient page context. Multi-record selections and
    list/report pages have no single owner binding. A malformed detail policy
    fails closed rather than silently widening the query to all records.
    """

    if ctx.record_context is not None:
        records = ctx.record_context.records
        if len(records) != 1:
            return None
        record = records[0]
        resource_type = canonical_resource_type(record.resource_type)
        if not resource_type or not record.record_id:
            raise UnknownPageContextProfileError("explicit record context has no canonical owner")
        return BusinessQueryOwnerHint(
            resource_type=resource_type,
            record_id=record.record_id,
        )
    policy = ctx.policy
    if policy is None or policy.kind != "detail":
        return None
    if not policy.owner_id:
        raise UnknownPageContextProfileError("detail page context has no authorized owner")

    resource_type = canonical_resource_type(policy.resource_type)
    if not resource_type:
        raise UnknownPageContextProfileError("detail page context has no canonical resource")
    return BusinessQueryOwnerHint(resource_type=resource_type, record_id=policy.owner_id)
