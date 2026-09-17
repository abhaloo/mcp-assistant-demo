"""Cube REST adapter — fake-transport mapping proof only (no Cube deploy)."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.auth import Principal
from app.business_query.authorize.capability import allowed_filter_values_valid
from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.compile.business_time import period_bounds
from app.business_query.definitions import (
    DefinitionBundle,
    measure_for_member,
    resolve_member,
)
from app.business_query.outcomes import (
    AdapterUnsupported,
    Answered,
    Incomplete,
    PlanRefused,
)
from app.business_query.plan import (
    AttributePredicate,
    BusinessPeriod,
    BusinessQueryPlan,
    FilterGroup,
    FilterOperator,
    PlanFilter,
    iter_filter_leaves,
)
from app.business_query.seal.evidence import (
    AdapterExecutionEvidence,
    declared_result_members,
    normalize_event_rows,
    result_columns,
    seal_adapter_result,
)
from app.business_query.wire.module import UnsealedAdapterAnswer

logger = logging.getLogger(__name__)

CubeTransport = Callable[[dict[str, Any]], dict[str, Any]]

_OPERATOR_MAP: dict[FilterOperator, str] = {
    "eq": "equals",
    "neq": "notEquals",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "in": "equals",
    "not_in": "notEquals",
    "is_null": "notSet",
    "not_null": "set",
}

_FALLTHROUGH_LOGS: dict[str, str] = {
    "bucket_set_unsupported": "cube adapter falling through: bucket_set unsupported",
    "compare_to_unsupported": "cube adapter falling through: compare_to unsupported",
    "derived_measure_unsupported": "cube adapter falling through: derived measure unsupported",
    "segment_unsupported": "cube adapter falling through: segment unsupported",
}


class CubeAdapter:
    """Map a ScopedPlan to Cube JSON; transport is injected (fake in release 1)."""

    def __init__(
        self,
        *,
        transport: CubeTransport,
        principal: Principal,
        bundle: DefinitionBundle,
        model_revision: str,
        expected_bundle_hash: str | None = None,
        database_identity: str | None = None,
    ) -> None:
        self._transport = transport
        self._principal = principal
        self._bundle = bundle
        self._model_revision = model_revision
        self._expected_bundle_hash = (
            expected_bundle_hash if expected_bundle_hash is not None else bundle.content_hash
        )
        self._database_identity = database_identity

    def execute(self, scoped: ScopedPlan, *, trace: object | None = None) -> Answered | Incomplete:
        return seal_adapter_result(
            self._execute(scoped), scoped, principal=self._principal, bundle=self._bundle
        )

    def execute_with_evidence(
        self, scoped: ScopedPlan, *, trace: object | None = None
    ) -> UnsealedAdapterAnswer | Incomplete:
        return self._execute(scoped)

    def _execute(self, scoped: ScopedPlan) -> UnsealedAdapterAnswer | Incomplete:
        if self._bundle.content_hash != self._expected_bundle_hash:
            logger.warning("cube adapter incomplete: bundle content_hash mismatch")
            return Incomplete(reason_code="adapter_invalid")
        if scoped.bundle_hash is not None and scoped.bundle_hash != self._bundle.content_hash:
            logger.warning("cube adapter incomplete: scoped bundle_hash mismatch")
            return Incomplete(reason_code="adapter_invalid")
        if scoped.principal is not None and scoped.principal != self._principal:
            logger.warning("cube adapter incomplete: principal mismatch")
            return Incomplete(reason_code="adapter_invalid")
        if not allowed_filter_values_valid(scoped.plan, self._bundle):
            logger.warning("cube adapter incomplete: filter value outside declared domain")
            return Incomplete(reason_code="adapter_invalid")
        unsupported = _unsupported_plan_reason(scoped.plan, self._bundle)
        if unsupported is not None:
            logger.warning(_FALLTHROUGH_LOGS[unsupported])
            raise AdapterUnsupported(unsupported)
        if (
            scoped.plan.period is not None
            and (scoped.plan.period.relative is not None or scoped.plan.period.since is not None)
            and scoped.business_date is None
        ):
            logger.warning("cube adapter incomplete: relative period without business_date")
            return Incomplete(reason_code="adapter_invalid")
        if scoped.forced:
            # Forced predicates (the server-imposed scope channel) are not
            # translated into Cube filters in release 1 — a genuine capability
            # gap, not a terminal failure. Silently dropping the scope boundary
            # would be worse, so this still refuses; AdapterUnsupported instead
            # of Incomplete lets the chain fall through.
            logger.warning("cube adapter falling through: forced scope predicates unsupported")
            raise AdapterUnsupported("forced_scope_unsupported")
        identity = self._database_identity
        if not identity:
            return Incomplete(reason_code="adapter_invalid")

        try:
            started_at = datetime.now(tz=UTC)
            payload = {
                "query": plan_to_cube_query(
                    scoped.plan,
                    business_date=scoped.business_date,
                    business_timezone=self._bundle.business_timezone,
                ),
                "securityContext": principal_security_context(scoped.principal or self._principal),
                "bundleHash": self._bundle.content_hash,
                "modelRevision": self._model_revision,
            }
            response = self._transport(payload)
            finished_at = datetime.now(tz=UTC)
        except (PlanRefused, AdapterUnsupported):
            # Both refusal shapes originate from plan_to_cube_query building
            # the payload above (a set operator raises AdapterUnsupported;
            # see _filter_node_to_cube) -- they must propagate, not fall into
            # the broad transport-error handler below and get silently
            # rewritten into Incomplete.
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("cube adapter incomplete: transport error %s", type(exc).__name__)
            return Incomplete(reason_code="adapter_invalid")

        return self._parse_response(
            response,
            scoped.plan,
            payload=payload,
            started_at=started_at,
            finished_at=finished_at,
            database_identity=identity,
        )

    def _parse_response(
        self,
        response: dict[str, Any],
        plan: BusinessQueryPlan,
        *,
        payload: dict[str, Any],
        started_at: datetime,
        finished_at: datetime,
        database_identity: str,
    ) -> UnsealedAdapterAnswer | Incomplete:
        if not isinstance(response, dict):
            logger.warning("cube adapter incomplete: malformed response (not a dict)")
            return Incomplete(reason_code="adapter_invalid")
        if response.get("status") == 503 or "error" in response:
            logger.warning("cube adapter incomplete: outage or error response")
            return Incomplete(reason_code="adapter_invalid")
        data = response.get("data")
        if not isinstance(data, list):
            logger.warning("cube adapter incomplete: malformed response (data not a list)")
            return Incomplete(reason_code="adapter_invalid")

        rows = [row for row in data if isinstance(row, dict)]
        if len(rows) != len(data):
            logger.warning("cube adapter incomplete: malformed response (row shape)")
            return Incomplete(reason_code="adapter_invalid")

        total_row_count = response.get("total", len(rows))
        if not isinstance(total_row_count, int) or total_row_count < len(rows):
            logger.warning("cube adapter incomplete: malformed response (total row count)")
            return Incomplete(reason_code="adapter_invalid")

        result_keys = (
            list(rows[0])
            if rows
            else [*plan.measures, *plan.dimensions]
            + ([plan.bucket_set] if plan.bucket_set is not None else [])
        )
        try:
            members = declared_result_members(plan, self._bundle, result_keys, rows)
            columns = result_columns(plan, self._bundle, members)
            event_rows = normalize_event_rows(rows, members)
        except (TypeError, ValueError):
            logger.warning("cube adapter incomplete: result member contract mismatch")
            return Incomplete(reason_code="adapter_invalid")
        canonical_query = json.dumps(
            payload["query"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        scope_payload = json.dumps(
            {
                "securityContext": payload["securityContext"],
                "bundleHash": payload["bundleHash"],
                "modelRevision": payload["modelRevision"],
            },
            default=str,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        answer_text = f"Returned {len(rows)} row(s)." if rows else "No matching rows."
        # record_refs is a deliberate no-op here (ADR 0053 Decision 4): Ask only
        # injects InternalCompilerAdapter, never Cube, so this adapter has no
        # bindable-id sidecar to mint. Leaving the field at its
        # UnsealedAdapterAnswer default (()) is correct, not an oversight.
        return UnsealedAdapterAnswer(
            answer_text=answer_text,
            rows=list(event_rows),
            total_row_count=total_row_count,
            evidence=AdapterExecutionEvidence(
                adapter="cube",
                backend="cube",
                compiled_query_digest=hashlib.sha256(canonical_query).hexdigest(),
                parameter_scope_digest=hashlib.sha256(scope_payload).hexdigest(),
                result_members=members,
                result_rows=event_rows,
                total_row_count=total_row_count,
                truncated=total_row_count > len(event_rows),
                database_identity=database_identity,
                started_at=started_at,
                finished_at=finished_at,
                result_columns=columns,
                # Cube has no keyset translation, so a Cube answer never offers a cursor.
                pageable=False,
            ),
        )


def principal_security_context(principal: Principal) -> dict[str, Any]:
    """Exact principal facts for Cube queryRewrite — never planner-supplied."""
    return {
        "user_id": principal.user_id,
        "role": principal.role,
        "permissions": list(principal.permissions or []),
        "entity_id": principal.entity_id,
        "cross_entity": principal.cross_entity,
        "department_id": (
            principal.scope_values.department_id
            if principal.scope_values
            else principal.department_id
        ),
    }


def plan_to_cube_query(
    plan: BusinessQueryPlan,
    *,
    business_date=None,
    business_timezone: str = "UTC",
) -> dict[str, Any]:
    """Typed plan → Cube ``Query`` field names (CUBE §5)."""
    query: dict[str, Any] = {
        "measures": list(plan.measures),
        "dimensions": list(plan.dimensions),
        "limit": plan.limit,
    }
    filters = _filters_to_cube(plan.filters) + _filters_to_cube(plan.having)
    if filters:
        query["filters"] = filters
    if plan.period is not None:
        query["timeDimensions"] = [
            _period_to_time_dimension(
                plan.period, business_date=business_date, business_timezone=business_timezone
            )
        ]
    if plan.order:
        query["order"] = {clause.member: clause.direction for clause in plan.order}
    return query


def _filters_to_cube(group: FilterGroup | None) -> list[dict[str, Any]]:
    if group is None:
        return []
    expression = _filter_group_to_cube(group)
    return [expression] if expression is not None else []


def _filter_group_to_cube(group: FilterGroup) -> dict[str, Any] | None:
    all_parts = [_filter_node_to_cube(node) for node in group.all]
    any_parts = [_filter_node_to_cube(node) for node in group.any]
    parts = [part for part in all_parts if part is not None]
    if any_parts:
        parts.append({"or": [part for part in any_parts if part is not None]})
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"and": parts}


def _filter_node_to_cube(
    node: PlanFilter | AttributePredicate | FilterGroup,
) -> dict[str, Any] | None:
    if isinstance(node, FilterGroup):
        return _filter_group_to_cube(node)
    member = node.family_key if isinstance(node, AttributePredicate) else node.member
    if node.operator in {"in_set", "not_in_set"}:
        # A derived-set membership filter IS expressible by
        # InternalCompilerAdapter -- this refusal must let module_scoping's
        # adapter loop fall through to it, not treat every adapter as
        # refused (PlanRefused is terminal there; AdapterUnsupported
        # continues to the next adapter).
        raise AdapterUnsupported("unsupported_operator")
    operator = _OPERATOR_MAP.get(node.operator)
    if operator is None:
        raise PlanRefused("unsupported_operator")
    item: dict[str, Any] = {"member": member, "operator": operator}
    if node.operator not in {"is_null", "not_null"}:
        item["values"] = [str(v) for v in node.values]
    return item


def _period_to_time_dimension(
    period: BusinessPeriod, *, business_date=None, business_timezone: str = "UTC"
) -> dict[str, Any]:
    td: dict[str, Any] = {"dimension": period.time_dimension}
    if period.granularity is not None:
        td["granularity"] = period.granularity
    start, end = period_bounds(period, business_timezone, business_date=business_date)
    td["dateRange"] = [start.isoformat(), (end - timedelta(days=1)).isoformat()]
    return td


def _unsupported_plan_reason(plan: BusinessQueryPlan, bundle: DefinitionBundle) -> str | None:
    """The first Cube capability gap the plan names, in fall-through order."""
    if plan.bucket_set is not None:
        # No bucket-case translation in release 1; fall through to an adapter that
        # can express it (ADR 0047 Inv.8).
        return "bucket_set_unsupported"
    if plan.compare_to is not None:
        # One Cube query answers one period. Answering the current period alone
        # would read as a previous period with no data, so refuse and fall through.
        return "compare_to_unsupported"
    for member in plan.measures:
        measure = measure_for_member(bundle, member)
        if measure is not None and measure.expression_kind is not None:
            # Ratio and share arithmetic is compiler-owned; Cube has no such member.
            return "derived_measure_unsupported"
    for group in (plan.filters, plan.having):
        if group is None:
            continue
        for leaf in iter_filter_leaves(group):
            if isinstance(leaf, PlanFilter):
                resolved = resolve_member(bundle, leaf.member)
                if resolved is not None and resolved[0] == "segment":
                    # A segment is a compiler predicate over row columns, not a Cube member.
                    return "segment_unsupported"
    return None
