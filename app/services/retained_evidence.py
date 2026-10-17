"""Retained business query members and stored plan conversion (spec §7, Task R6a)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.outcomes import Answered as DomainAnswered
from app.business_query.plan import plan_fingerprint
from app.business_query.wire.ask_result import RetainedBqMember

MAX_TURN_TABLES = 4


class RetentionMismatchError(ValueError):
    """Raised when member count, ordinals, receipt IDs, or fingerprints do not match."""


def retained_members(outcome: DomainAnswered) -> tuple[RetainedBqMember, ...]:
    """Extract and validate retained BQ members from domain Answered.

    Raises RetentionMismatchError when member count, ordinals, or receipt IDs
    fail correspondence invariants. Bounded to at most ``MAX_TURN_TABLES`` members.
    """
    if outcome.plan is None and not outcome.companion_answered:
        return ()
    if outcome.plan is None and outcome.companion_answered:
        raise RetentionMismatchError("Primary member missing plan while companion exists")
    if not outcome.receipt or not outcome.receipt.answer_query_id:
        raise RetentionMismatchError("Primary member missing answer_query_id")
    if not outcome.scope_fingerprint:
        raise RetentionMismatchError("Primary member missing scope_fingerprint")

    primary = RetainedBqMember(
        ordinal=1,
        answer_query_id=outcome.receipt.answer_query_id,
        plan=outcome.plan,
        plan_fingerprint=plan_fingerprint(outcome.plan),
        scope_fingerprint=outcome.scope_fingerprint,
        derived_payload=outcome.derived_payload,
        owner_hint=outcome.owner_hint,
    )
    members = [primary]
    seen_ids = {primary.answer_query_id}

    for index, comp in enumerate(outcome.companion_answered):
        if comp.plan is None:
            raise RetentionMismatchError("Companion member missing plan")
        if not comp.receipt or not comp.receipt.answer_query_id:
            raise RetentionMismatchError("Companion member missing answer_query_id")
        if comp.receipt.answer_query_id in seen_ids:
            raise RetentionMismatchError("Companion member has duplicate answer_query_id")
        if not comp.scope_fingerprint:
            raise RetentionMismatchError("Companion member missing scope_fingerprint")
        seen_ids.add(comp.receipt.answer_query_id)
        members.append(
            RetainedBqMember(
                ordinal=index + 2,
                answer_query_id=comp.receipt.answer_query_id,
                plan=comp.plan,
                plan_fingerprint=plan_fingerprint(comp.plan),
                scope_fingerprint=comp.scope_fingerprint,
                derived_payload=comp.derived_payload,
                owner_hint=comp.owner_hint,
            )
        )

    if len(members) > MAX_TURN_TABLES:
        raise RetentionMismatchError("at most 4 retained members")
    return tuple(members)


def to_stored_plan(
    member: RetainedBqMember,
    *,
    created_at: datetime,
    expires_at: datetime,
    principal: str | int | None,
    project_id: str | None = "default",
    entity_id: str | int | None = None,
    department_id: str | int | None = None,
    bundle_hash: str | None = None,
    policy_hash: str | None = None,
    total_row_count: int | None = None,
    response_policy: Literal["allow_partial", "strict"] = "allow_partial",
) -> StoredPlan:
    """Validate member correspondence invariants and convert to a StoredPlan."""
    if not 1 <= member.ordinal <= MAX_TURN_TABLES:
        raise RetentionMismatchError(
            f"invalid member ordinal: {member.ordinal} (must be 1 to {MAX_TURN_TABLES})"
        )
    if not member.answer_query_id:
        raise RetentionMismatchError("answer_query_id is required")
    if member.plan.derived_sets and member.derived_payload is None:
        raise RetentionMismatchError("derived plan without its scope envelope")

    calculated_fp = plan_fingerprint(member.plan)
    if member.plan_fingerprint != calculated_fp:
        raise RetentionMismatchError(
            f"plan_fingerprint mismatch: {member.plan_fingerprint} != {calculated_fp}"
        )
    if not member.scope_fingerprint:
        raise RetentionMismatchError("scope_fingerprint is required")

    return StoredPlan(
        answer_query_id=member.answer_query_id,
        plan=member.plan,
        plan_fingerprint=member.scope_fingerprint,
        created_at=created_at,
        expires_at=expires_at,
        principal=str(principal) if principal is not None else None,
        project_id=project_id,
        entity_id=entity_id,
        department_id=department_id,
        bundle_hash=bundle_hash,
        policy_hash=policy_hash,
        total_row_count=total_row_count,
        forced=member.derived_payload.forced if member.derived_payload is not None else (),
        response_policy=(
            member.derived_payload.response_policy
            if member.derived_payload is not None
            else response_policy
        ),
        original_question=(
            member.derived_payload.original_question if member.derived_payload is not None else None
        ),
        derived_payload=member.derived_payload,
        owner_hint=member.owner_hint,
    )
