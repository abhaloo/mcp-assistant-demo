"""Retained business query members and stored plan conversion (spec §7, Task R6a)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from app.business_query.compile.pagination.plan_store import StoredPlan
from app.business_query.outcomes import Answered as DomainAnswered
from app.business_query.plan import BusinessQueryPlan, plan_fingerprint

if TYPE_CHECKING:
    from app.services.ask_outcome import Answered as ServiceAnswered


@dataclass(frozen=True)
class RetainedBqMember:
    """One typed BQ member preserving plan and fingerprints before wire serialization."""

    ordinal: int  # 1 = primary, 2 = companion; matches BusinessQueryWireOutcome.envelopes order
    answer_query_id: str  # UnifiedResultEnvelope.answer_query_id
    plan: BusinessQueryPlan
    plan_fingerprint: str
    scope_fingerprint: str


class RetentionMismatchError(ValueError):
    """Raised when member count, ordinals, receipt IDs, or fingerprints do not match."""


def retained_members(outcome: DomainAnswered | ServiceAnswered) -> tuple[RetainedBqMember, ...]:
    """Extract and validate retained BQ members from domain Answered.

    Raises RetentionMismatchError when member count, ordinals, or receipt IDs
    fail correspondence invariants. Bounded to at most two members (primary + 1 companion).
    """
    if isinstance(outcome, DomainAnswered):
        if outcome.plan is None and not outcome.companion_answered:
            return ()
        if outcome.plan is None and outcome.companion_answered:
            raise RetentionMismatchError("Primary member missing plan while companion exists")
        if not outcome.receipt or not outcome.receipt.answer_query_id:
            raise RetentionMismatchError("Primary member missing answer_query_id")
        if not outcome.scope_fingerprint:
            raise RetentionMismatchError("Primary member missing scope_fingerprint")

        primary_fp = plan_fingerprint(outcome.plan)
        primary = RetainedBqMember(
            ordinal=1,
            answer_query_id=outcome.receipt.answer_query_id,
            plan=outcome.plan,
            plan_fingerprint=primary_fp,
            scope_fingerprint=outcome.scope_fingerprint,
        )
        members = [primary]

        if outcome.companion_answered:
            if len(outcome.companion_answered) > 1:
                count = len(outcome.companion_answered) + 1
                raise RetentionMismatchError(
                    f"At most one companion member allowed (bound to two members, got {count})"
                )
            comp = outcome.companion_answered[0]
            if comp.plan is None:
                raise RetentionMismatchError("Companion member missing plan")
            if not comp.receipt or not comp.receipt.answer_query_id:
                raise RetentionMismatchError("Companion member missing answer_query_id")
            if comp.receipt.answer_query_id == primary.answer_query_id:
                raise RetentionMismatchError("Companion member has duplicate answer_query_id")
            if not comp.scope_fingerprint:
                raise RetentionMismatchError("Companion member missing scope_fingerprint")

            comp_fp = plan_fingerprint(comp.plan)
            comp_member = RetainedBqMember(
                ordinal=2,
                answer_query_id=comp.receipt.answer_query_id,
                plan=comp.plan,
                plan_fingerprint=comp_fp,
                scope_fingerprint=comp.scope_fingerprint,
            )
            members.append(comp_member)

        return tuple(members)

    # Check for service Answered or adapter wrappers
    if hasattr(outcome, "retained_members") and outcome.retained_members:
        return outcome.retained_members
    if (
        hasattr(outcome, "bq")
        and outcome.bq is not None
        and getattr(outcome.bq, "retained_members", None)
    ):
        return outcome.bq.retained_members
    if (
        hasattr(outcome, "bq")
        and outcome.bq is not None
        and outcome.bq.plan is not None
        and outcome.bq.scope_fingerprint
        and hasattr(outcome, "business_query")
        and outcome.business_query is not None
        and outcome.business_query.envelope is not None
    ):
        return (
            RetainedBqMember(
                ordinal=1,
                answer_query_id=outcome.business_query.envelope.answer_query_id,
                plan=outcome.bq.plan,
                plan_fingerprint=plan_fingerprint(outcome.bq.plan),
                scope_fingerprint=outcome.bq.scope_fingerprint,
            ),
        )

    return ()


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
    if member.ordinal not in (1, 2):
        raise RetentionMismatchError(f"invalid member ordinal: {member.ordinal} (must be 1 or 2)")
    if not member.answer_query_id:
        raise RetentionMismatchError("answer_query_id is required")
    if member.plan.derived_sets:
        raise RetentionMismatchError("derived_sets not supported for retained plan")

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
        response_policy=response_policy,
    )
