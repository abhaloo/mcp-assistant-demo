"""Plan persistence models and storage protocols."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.business_query.authorize.scoping import ForcedPredicate
from app.business_query.compile.pagination.plan_payload import DerivedPlanPayload
from app.business_query.plan import BusinessQueryPlan, canonical_plan_payload

if TYPE_CHECKING:
    from app.query_records.model import BusinessQueryPlanRow


class StoredPlan(BaseModel):
    """Durable normalized BusinessQueryPlan payload with TTL expiration."""

    model_config = ConfigDict(strict=True, extra="forbid")

    answer_query_id: str
    plan: BusinessQueryPlan
    plan_fingerprint: str
    created_at: datetime
    expires_at: datetime
    principal: str | int | None = None
    project_id: str | None = None
    entity_id: str | int | None = None
    department_id: str | int | None = None
    bundle_hash: str | None = None
    policy_hash: str | None = None
    total_row_count: int | None = None
    forced: tuple[ForcedPredicate, ...] = ()
    response_policy: Literal["allow_partial", "strict"] = "allow_partial"
    original_question: str | None = None
    derived_payload: DerivedPlanPayload | None = None

    @model_validator(mode="after")
    def _validate_derived_payload_contract(self) -> StoredPlan:
        has_sets = bool(self.plan.derived_sets)
        if has_sets:
            if self.derived_payload is None:
                raise ValueError("derived_payload is required when plan contains derived_sets")
            if self.derived_payload.plan != self.plan:
                raise ValueError("derived_payload.plan must match StoredPlan.plan")
            if self.derived_payload.forced != self.forced:
                raise ValueError("derived_payload.forced must match StoredPlan.forced")
            if self.derived_payload.response_policy != self.response_policy:
                raise ValueError(
                    "derived_payload.response_policy must match StoredPlan.response_policy"
                )
            if self.derived_payload.original_question != self.original_question:
                raise ValueError(
                    "derived_payload.original_question must match StoredPlan.original_question"
                )
        else:
            if self.derived_payload is not None:
                raise ValueError("derived_payload must be None for non-set plans")
        return self


class InMemoryPlanStore:
    """In-memory concurrent PlanStore implementation for testing and ephemeral execution."""

    def __init__(self) -> None:
        self._plans: dict[str, StoredPlan] = {}
        self._lock = asyncio.Lock()

    async def save_plan(self, stored: StoredPlan) -> StoredPlan:
        async with self._lock:
            existing = self._plans.get(stored.answer_query_id)
            if existing is not None:
                return existing
            self._plans[stored.answer_query_id] = stored
            return stored

    async def get_plan(
        self, answer_query_id: str, *, now: datetime | None = None
    ) -> StoredPlan | None:
        async with self._lock:
            stored = self._plans.get(answer_query_id)
        if stored is None:
            return None
        current = now or datetime.now(tz=UTC)
        if current >= stored.expires_at:
            return None
        return stored


class PostgresPlanStore:
    """PostgreSQL PlanStore implementation persisting into query_records / business_query_plans."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_plan(self, stored: StoredPlan) -> StoredPlan:
        from app.query_records.model import BusinessQueryPlanRow

        if stored.derived_payload is not None:
            plan_payload = json.dumps(
                stored.derived_payload.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            plan_payload = json.dumps(
                {
                    "plan": canonical_plan_payload(stored.plan),
                    "forced": [item.model_dump(mode="json") for item in stored.forced],
                    "response_policy": stored.response_policy,
                    "original_question": stored.original_question,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        stmt = pg_insert(BusinessQueryPlanRow).values(
            answer_query_id=stored.answer_query_id,
            plan_payload=plan_payload,
            plan_fingerprint=stored.plan_fingerprint,
            created_at=stored.created_at,
            expires_at=stored.expires_at,
            principal=str(stored.principal) if stored.principal is not None else None,
            entity_id=str(stored.entity_id) if stored.entity_id is not None else None,
            department_id=str(stored.department_id) if stored.department_id is not None else None,
            project_id=stored.project_id,
            manifest_hash=stored.policy_hash,
            bundle_hash=stored.bundle_hash,
        )
        # First writer wins: a retry must not overwrite the row an already-minted
        # cursor points at.
        stmt = stmt.on_conflict_do_nothing(index_elements=["answer_query_id"])
        await self._session.execute(stmt)
        await self._session.flush()
        row = await self._load_row(stored.answer_query_id)
        if row is None:
            raise RuntimeError("plan row missing after save")
        return _stored_plan_from_row(row)

    async def get_plan(
        self, answer_query_id: str, *, now: datetime | None = None
    ) -> StoredPlan | None:
        current = now or datetime.now(tz=UTC)
        row = await self._load_row(answer_query_id)
        if row is None:
            return None
        if current >= row.expires_at:
            return None
        return _stored_plan_from_row(row)

    async def _load_row(self, answer_query_id: str) -> BusinessQueryPlanRow | None:
        from app.query_records.model import BusinessQueryPlanRow

        stmt = select(BusinessQueryPlanRow).where(
            BusinessQueryPlanRow.answer_query_id == answer_query_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


def _stored_plan_from_row(row: BusinessQueryPlanRow) -> StoredPlan:
    decoded = json.loads(row.plan_payload)
    if isinstance(decoded, dict) and decoded.get("format_version") == 2:
        derived_payload = DerivedPlanPayload.model_validate_json(row.plan_payload)
        plan = derived_payload.plan
        forced = derived_payload.forced
        response_policy = derived_payload.response_policy
        original_question = derived_payload.original_question
    elif isinstance(decoded, dict) and "format_version" in decoded:
        raise ValueError(f"unsupported stored plan format_version: {decoded['format_version']}")
    elif isinstance(decoded, dict) and "plan" in decoded:
        plan = BusinessQueryPlan.model_validate(decoded["plan"])
        if plan.derived_sets:
            raise ValueError("legacy stored plan payload cannot contain derived_sets")
        forced = tuple(ForcedPredicate.model_validate(value) for value in decoded.get("forced", []))
        response_policy = decoded.get("response_policy", "allow_partial")
        original_question = decoded.get("original_question")
        derived_payload = None
    else:
        plan = BusinessQueryPlan.model_validate(decoded)
        if plan.derived_sets:
            raise ValueError("legacy stored plan payload cannot contain derived_sets")
        forced = ()
        response_policy = "allow_partial"
        original_question = None
        derived_payload = None
    return StoredPlan(
        answer_query_id=row.answer_query_id,
        plan=plan,
        plan_fingerprint=row.plan_fingerprint,
        created_at=row.created_at,
        expires_at=row.expires_at,
        principal=row.principal,
        entity_id=row.entity_id,
        project_id=row.project_id,
        department_id=row.department_id,
        policy_hash=row.manifest_hash,
        bundle_hash=row.bundle_hash,
        forced=forced,
        response_policy=response_policy,
        original_question=original_question,
        derived_payload=derived_payload,
    )
