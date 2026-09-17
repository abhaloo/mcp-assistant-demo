"""ResultPageExecutor for executing keyset-paginated queries without planner invocation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from datetime import UTC, datetime
from typing import Any

from app.auth import Principal
from app.business_query.authorize.scoping import (
    ForcedPredicate,
    ScopedDerivedSet,
    ScopeDenied,
    ScopedPlan,
    apply_role_scope,
    bind_scope_context,
    canonical_forced,
)
from app.business_query.compile.pagination.keyset import apply_keyset_to_plan
from app.business_query.compile.pagination.plan_payload import stored_plan_fingerprint
from app.business_query.compile.pagination.scope_binding import ScopeBinding
from app.business_query.definitions import (
    BundleSelectionError,
    BundleValidationError,
    DefinitionBundle,
    InvalidBundleIndexError,
    bundle_for_manifest,
)
from app.business_query.outcomes import (
    Answered,
    BusinessQueryOutcome,
    Denied,
    Incomplete,
    NextPageAction,
    PlanRefused,
    Unsupported,
)
from app.business_query.plan import BusinessQueryPlan, plan_fingerprint
from app.business_query.ports import EvidenceExecutionAdapter, ExecutionAdapter, PlanStore
from app.business_query.seal.action_tokens import ResultPageCursor
from app.business_query.seal.evidence import UnsealedAdapterAnswer
from app.business_query.wire.comparison_presenter import present_for_plan
from app.config import settings
from app.core.errors import DeadlineExpiredError
from app.core.turn_budget import (
    UNBOUNDED_BUDGET,
    TurnBudget,
    await_with_budget,
    run_blocking_with_budget,
)

logger = logging.getLogger(__name__)

# Result-page signing secret and deny copy, shared by the mint and verify paths.
_DEFAULT_SECRET = settings.rag_jwt_secret
_DENY_MESSAGE = "business query tools are currently unavailable"


def _reconcile_forced_predicates(
    reauthorized_forced: tuple[ForcedPredicate, ...],
    stored_forced: tuple[ForcedPredicate, ...],
    *,
    is_derived: bool = False,
) -> tuple[ForcedPredicate, ...]:
    """Reconcile reauthorized forced predicates against stored forced predicates."""
    curr_principal = [p for p in reauthorized_forced if p.source == "principal_scope"]
    stored_principal = [p for p in stored_forced if p.source == "principal_scope"]

    if stored_principal or is_derived:
        if canonical_forced(curr_principal) != canonical_forced(stored_principal):
            raise ScopeDenied("principal scope mismatch")

    curr_record = [p for p in reauthorized_forced if p.source == "record_referent"]
    stored_record = [p for p in stored_forced if p.source == "record_referent"]

    if curr_record and canonical_forced(curr_record) != canonical_forced(stored_record):
        raise ScopeDenied("record referent mismatch")

    merged = list(curr_principal)
    seen = {item.model_dump_json() for item in merged}
    for item in stored_record:
        dump = item.model_dump_json()
        if dump not in seen:
            merged.append(item)
            seen.add(dump)
    return tuple(merged)


class ResultPageExecutor:
    """Executes subsequent result pages using a signed ResultPageCursor."""

    def __init__(
        self,
        *,
        plan_store: PlanStore,
        assert_set_context_fits: Callable[[BusinessQueryPlan, int], None],
        finalize_answer_text: Callable[..., Answered],
        bundle_resolver: Callable[[str], DefinitionBundle] | None = None,
        adapters: Sequence[ExecutionAdapter] | None = None,
        scope_fn: (
            Callable[[BusinessQueryPlan, Principal, DefinitionBundle], ScopedPlan] | None
        ) = None,
        presenter: Callable[[BusinessQueryPlan, Answered], str] | None = None,
        planner: Any | None = None,
        secret: str | None = None,
        project_id: str = "default",
        evidence_sealer: Callable[..., Any] | None = None,
        receipt_id_factory: Callable[[ResultPageCursor, str | None], str] | None = None,
        turn_budget: TurnBudget = UNBOUNDED_BUDGET,
        executor: Executor | None = None,
        step_timeout_seconds: float = 10.0,
        terminal_reserve_seconds: float = 2.0,
        max_answer_chars: int = 8_000,
    ) -> None:
        self._plan_store = plan_store
        # Injected, not imported here: wire/answer_finalization.py sits above
        # this pagination package in the compile->wire direction, so the
        # caller supplies these instead of this module reaching up for them
        # (see docs/superpowers/import-cycles-baseline.json).
        self._assert_set_context_fits = assert_set_context_fits
        self._finalize_answer_text = finalize_answer_text
        self._bundle_resolver = bundle_resolver or bundle_for_manifest
        self._adapters = list(adapters or [])
        self._scope_fn = scope_fn or apply_role_scope
        self._presenter = presenter or present_for_plan
        self._planner = planner  # Preserved for interface compatibility; never invoked
        self._secret = secret or _DEFAULT_SECRET
        self._project_id = project_id
        self._evidence_sealer = evidence_sealer
        self._receipt_id_factory = receipt_id_factory
        self._turn_budget = turn_budget
        self._executor = executor
        self._step_timeout_seconds = step_timeout_seconds
        self._terminal_reserve_seconds = terminal_reserve_seconds
        self._max_answer_chars = max_answer_chars

    async def _await_step(self, operation: Callable[[], Any]) -> Any:
        return await await_with_budget(
            operation,
            self._turn_budget,
            ceiling_seconds=self._step_timeout_seconds,
            reserve_seconds=self._terminal_reserve_seconds,
        )

    async def _run_sync(self, operation: Callable[[], Any]) -> Any:
        if self._executor is None:

            async def _on_loop() -> Any:
                return operation()

            return await await_with_budget(
                _on_loop,
                self._turn_budget,
                ceiling_seconds=self._step_timeout_seconds,
                reserve_seconds=self._terminal_reserve_seconds,
            )
        return await run_blocking_with_budget(
            operation,
            self._turn_budget,
            executor=self._executor,
            ceiling_seconds=self._step_timeout_seconds,
            reserve_seconds=self._terminal_reserve_seconds,
        )

    async def _run_adapters_for_page(
        self,
        scoped: ScopedPlan,
        *,
        principal: Principal,
        cursor: ResultPageCursor,
        idempotency_key: str | None,
    ) -> BusinessQueryOutcome | None:
        for adapter in self._adapters:
            try:
                if self._evidence_sealer is not None:
                    if not isinstance(adapter, EvidenceExecutionAdapter):
                        return Incomplete(reason_code="adapter_invalid")
                    res = await self._run_sync(lambda a=adapter: a.execute_with_evidence(scoped))
                    if isinstance(res, UnsealedAdapterAnswer):
                        sealed = res
                        res = await self._await_step(
                            lambda: self._evidence_sealer(
                                sealed,
                                scoped,
                                principal,
                                cursor,
                                idempotency_key,
                            )
                        )
                else:
                    res = await self._run_sync(lambda a=adapter: a.execute(scoped))
                if isinstance(res, (Answered, Incomplete, Denied, Unsupported)):
                    return res
            except TimeoutError:
                return Incomplete(reason_code="timeout")
            except DeadlineExpiredError:
                raise
            except ScopeDenied:
                return Denied(message=_DENY_MESSAGE, reason_code="policy_denied")
            except Exception as exc:
                logger.warning("Pagination adapter failed: %s", exc, exc_info=True)
                return Incomplete(reason_code="adapter_invalid")
        return None

    async def execute(
        self,
        signed_cursor: ResultPageCursor | str,
        principal: Principal,
        idempotency_key: str | None = None,
        *,
        now: datetime | None = None,
    ) -> BusinessQueryOutcome:
        try:
            return await self._execute_page(signed_cursor, principal, idempotency_key, now=now)
        except DeadlineExpiredError:
            return Incomplete(reason_code="timeout")

    async def _execute_page(
        self,
        signed_cursor: ResultPageCursor | str,
        principal: Principal,
        idempotency_key: str | None = None,
        *,
        now: datetime | None = None,
    ) -> BusinessQueryOutcome:
        current_time = now or datetime.now(tz=UTC)

        # 1. Parse cursor
        if isinstance(signed_cursor, str):
            try:
                cursor = ResultPageCursor.decode(signed_cursor)
            except Exception:
                return Denied(message=_DENY_MESSAGE, reason_code="cursor_invalid")
        else:
            cursor = signed_cursor

        # 2. Verify signature
        if not cursor.verify_signature(self._secret):
            return Denied(message=_DENY_MESSAGE, reason_code="cursor_invalid")

        # 3. Check cursor expiration
        if current_time >= cursor.expires_at:
            return Incomplete(reason_code="cursor_expired")

        # 4. Scope bindings check (caller vs cursor)
        caller_scope = ScopeBinding.from_principal(principal, project_id=self._project_id)
        cursor_scope = ScopeBinding.from_cursor(cursor)
        if not cursor_scope.verify_caller(caller_scope, self._project_id):
            return Denied(message=_DENY_MESSAGE, reason_code="cursor_scope_mismatch")

        # 5. Load DefinitionBundle and verify bundle_hash
        manifest_hash = principal.manifest_hash or cursor.policy_hash
        try:
            bundle = self._bundle_resolver(manifest_hash)
        except (BundleSelectionError, BundleValidationError, InvalidBundleIndexError):
            return Denied(message=_DENY_MESSAGE, reason_code="policy_denied")
        except Exception:
            return Denied(message=_DENY_MESSAGE, reason_code="policy_denied")

        if cursor.bundle_hash and bundle.content_hash != cursor.bundle_hash:
            return Denied(message=_DENY_MESSAGE, reason_code="cursor_scope_mismatch")

        # 6. Fetch stored normalized plan
        plan_answer_query_id = cursor.plan_answer_query_id or cursor.answer_query_id
        stored = await self._await_step(
            lambda: self._plan_store.get_plan(plan_answer_query_id, now=current_time)
        )
        if stored is None:
            return Incomplete(reason_code="cursor_expired")
        if current_time >= stored.expires_at:
            return Incomplete(reason_code="cursor_expired")
        if stored.plan_fingerprint != cursor.plan_fingerprint:
            return Incomplete(reason_code="cursor_expired")
        try:
            expected_fingerprint = stored_plan_fingerprint(stored)
        except Exception:
            return Incomplete(reason_code="cursor_expired")
        legacy_fingerprint = (
            plan_fingerprint(stored.plan)
            if not stored.plan.derived_sets
            and not stored.forced
            and stored.response_policy == "allow_partial"
            else None
        )
        if stored.plan_fingerprint not in {expected_fingerprint, legacy_fingerprint}:
            return Incomplete(reason_code="cursor_expired")

        stored_scope = ScopeBinding.from_stored_plan(stored)
        if not stored_scope.verify_stored(
            cursor_scope,
            caller_scope,
            self._project_id,
            bundle.content_hash,
        ):
            return Denied(message=_DENY_MESSAGE, reason_code="cursor_scope_mismatch")

        try:
            self._assert_set_context_fits(stored.plan, self._max_answer_chars)
        except PlanRefused:
            logger.warning("set context budget exceeded in page executor")
            return Unsupported(
                reason_code="grain_unexpressible",
                message="plan cannot be executed safely",
            )

        # 7. Reauthorize plan, restore scope context, and apply keyset pagination
        try:
            scoped = self._scope_fn(stored.plan, principal, bundle)
            frozen_date = (
                stored.derived_payload.business_date if stored.derived_payload is not None else None
            )
            scoped = bind_scope_context(
                scoped,
                principal=principal,
                bundle_hash=bundle.content_hash,
                business_date=frozen_date,
                response_policy=stored.response_policy,
            )

            is_derived = stored.derived_payload is not None
            reconciled_outer_forced = _reconcile_forced_predicates(
                scoped.forced,
                stored.forced,
                is_derived=is_derived,
            )
            scoped = scoped.model_copy(update={"forced": reconciled_outer_forced})

            if stored.derived_payload is not None:
                snapshots_by_id = {s.id: s.forced for s in stored.derived_payload.derived}
                if set(snapshots_by_id.keys()) != {d.id for d in scoped.derived}:
                    raise ScopeDenied()
                reconciled_derived: list[ScopedDerivedSet] = []
                for d in scoped.derived:
                    inner_stored_forced = snapshots_by_id[d.id]
                    reconciled_inner_forced = _reconcile_forced_predicates(
                        d.scoped.forced,
                        inner_stored_forced,
                        is_derived=True,
                    )
                    reconciled_inner_scoped = d.scoped.model_copy(
                        update={"forced": reconciled_inner_forced}
                    )
                    reconciled_derived.append(
                        ScopedDerivedSet(
                            id=d.id,
                            key=d.key,
                            mode=d.mode,
                            scoped=reconciled_inner_scoped,
                        )
                    )
                scoped = scoped.model_copy(update={"derived": tuple(reconciled_derived)})

            paginated_plan = apply_keyset_to_plan(
                scoped.plan,
                bundle,
                cursor.keyset_position,
                cursor.page_size,
            )
            scoped = scoped.model_copy(update={"plan": paginated_plan})

            if self._evidence_sealer is not None:
                if self._receipt_id_factory is None:
                    return Incomplete(reason_code="adapter_invalid")
                scoped = scoped.model_copy(
                    update={"answer_query_id": self._receipt_id_factory(cursor, idempotency_key)}
                )
        except ScopeDenied:
            return Denied(message=_DENY_MESSAGE, reason_code="policy_denied")
        except Exception:
            return Incomplete(reason_code="adapter_invalid")

        # 9. Execute with adapter
        adapter_outcome = await self._run_adapters_for_page(
            scoped,
            principal=principal,
            cursor=cursor,
            idempotency_key=idempotency_key,
        )
        if adapter_outcome is None or not isinstance(adapter_outcome, Answered):
            return adapter_outcome or Incomplete(reason_code="adapter_invalid")

        # 10. Extract keyset position from last row if more rows might exist
        rows = adapter_outcome.rows
        next_cursor_token: str | None = None
        effective_total_count = (
            cursor.total_row_count
            if cursor.total_row_count is not None
            else (
                stored.total_row_count
                if stored.total_row_count is not None
                else adapter_outcome.total_row_count
            )
        )

        rows_seen = cursor.offset_row_count + len(rows)
        if (
            rows
            and len(rows) >= cursor.page_size
            and effective_total_count is not None
            and rows_seen < effective_total_count
        ):
            last_row = rows[-1]
            next_keyset: dict[str, Any] = {}
            for member in [clause.member for clause in paginated_plan.order]:
                if member in last_row:
                    next_keyset[member] = last_row[member]
            for dim in paginated_plan.dimensions:
                if dim in last_row and dim not in next_keyset:
                    next_keyset[dim] = last_row[dim]

            next_cursor = ResultPageCursor.mint(
                principal=principal,
                entity_id=principal.entity_id,
                department_id=(
                    principal.scope_values.department_id if principal.scope_values else None
                ),
                policy_hash=cursor.policy_hash,
                bundle_hash=cursor.bundle_hash,
                project_id=self._project_id,
                answer_query_id=adapter_outcome.receipt.answer_query_id,
                plan_answer_query_id=plan_answer_query_id,
                plan_fingerprint=cursor.plan_fingerprint,
                keyset_position=next_keyset,
                page_size=cursor.page_size,
                expires_at=cursor.expires_at,
                total_row_count=effective_total_count,
                offset_row_count=rows_seen,
                secret=self._secret,
            )
            next_cursor_token = next_cursor.encode()

        # 11. Present answer text
        try:
            answer_text = await self._run_sync(
                lambda: self._presenter(paginated_plan, adapter_outcome)
            )
        except DeadlineExpiredError:
            raise
        except Exception:
            answer_text = adapter_outcome.answer_text

        outcome = adapter_outcome.model_copy(
            update={
                "total_row_count": effective_total_count,
                "plan": paginated_plan,
                "next_cursor": next_cursor_token,
                "next_page_action": (
                    NextPageAction(
                        cursor=next_cursor_token,
                        page_size=cursor.page_size,
                        expires_at=cursor.expires_at,
                    )
                    if next_cursor_token is not None
                    else None
                ),
                "result_completeness": (
                    "partial"
                    if next_cursor_token is not None
                    else adapter_outcome.result_completeness
                ),
                "receipt": adapter_outcome.receipt.model_copy(
                    update={"source_question": stored.original_question}
                ),
            }
        )
        return self._finalize_answer_text(
            paginated_plan,
            outcome,
            text=answer_text,
            max_answer_chars=self._max_answer_chars,
        )
