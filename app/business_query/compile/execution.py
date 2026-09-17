"""Database execution error mapping and statement timeout inspection (ADR 0054)."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError

from app.auth import Principal
from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.compile.detail_engine import DetailEngine, DetailSelectionRefused
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Denied, Incomplete, RecordDetail, RecordRef

if TYPE_CHECKING:
    from app.business_query.wire.trace import QueryTrace

logger = logging.getLogger(__name__)

# MariaDB kills a query past max_statement_time with 1969; MySQL uses 3024 for
# its own MAX_EXECUTION_TIME. Both mean "too slow", never "malformed".
STATEMENT_TIMEOUT_CODES = frozenset({1969, 3024})
_DENY_MESSAGE = "business query tools are currently unavailable"


def is_statement_timeout(exc: SQLAlchemyError) -> bool:
    """True if exc was caused by a database statement timeout kill (1969 or 3024)."""
    args = getattr(getattr(exc, "orig", None), "args", ())
    return bool(args) and args[0] in STATEMENT_TIMEOUT_CODES


@dataclass(frozen=True)
class DetailReadOutcome:
    record_details: list[RecordDetail]
    failed_detail_families: tuple[str, ...]


def execute_detail_reads(
    *,
    detail_adapter: DetailEngine,
    bundle: DefinitionBundle,
    scoped: ScopedPlan,
    rows: list[dict[str, Any]],
    record_refs: Sequence[RecordRef],
    principal: Principal | None,
    trace: QueryTrace | None = None,
    writer: Any | None = None,
) -> DetailReadOutcome | Denied | Incomplete:
    if not scoped.plan.detail_selections or not bundle.detail_sources:
        return DetailReadOutcome([], ())
    detail_sources = {source.family_key: source for source in bundle.detail_sources}
    owner_resources = {
        detail_sources[selection.family].owner_resource
        for selection in scoped.plan.detail_selections
        if selection.family in detail_sources
    }
    parent_ids: list[int | str] = []
    for row in rows:
        for resource in sorted(owner_resources):
            member = f"{resource}.id"
            if member in row and row[member] is not None:
                parent_ids.append(row[member])
        if len(owner_resources) == 1 and "id" in row and row["id"] is not None:
            parent_ids.append(row["id"])
    if not parent_ids and record_refs:
        for ref in record_refs:
            if ref.resource in ("job", "work_order"):
                parent_ids.append(ref.record_id)
    if not parent_ids:
        return DetailReadOutcome([], ())

    entity_id = principal.entity_id if principal and principal.entity_id is not None else None
    if entity_id is None and scoped.principal and scoped.principal.entity_id is not None:
        entity_id = scoped.principal.entity_id
    if entity_id is None:
        for pred in scoped.forced:
            if pred.column == "entity_id" and pred.values:
                entity_id = pred.values[0]
                break
    if entity_id is None:
        logger.warning("detail read refused without an entity scope")
        return Denied(message=_DENY_MESSAGE, reason_code="policy_denied")

    department_scope_pairs = {
        (
            detail_sources[selection.family].owner_resource,
            detail_sources[selection.family].scope_columns.department,
        )
        for selection in scoped.plan.detail_selections
        if selection.family in detail_sources
        and detail_sources[selection.family].scope_columns.department is not None
    }
    department_required = any(
        (predicate.resource, predicate.column) in department_scope_pairs
        and predicate.operator == "eq"
        for predicate in scoped.forced
    )
    scoped_principal = scoped.principal or principal
    detail_started = time.perf_counter()
    record_details: list[RecordDetail] = []
    failed_detail_families: tuple[str, ...] = ()
    try:
        detail_result = detail_adapter.read_job_details_result(
            entity_id=entity_id,
            parent_ids=parent_ids,
            family_ids=scoped.plan.detail_selections,
            principal=scoped_principal,
            bundle=bundle,
            department_id=(
                scoped_principal.scope_values.department_id
                if scoped_principal.scope_values is not None
                else None
            ),
            department_required=department_required,
            trace=trace,
        )
        record_details = list(detail_result.details)
        failed_detail_families = detail_result.failed_families
    except (SQLAlchemyError, DetailSelectionRefused) as exc:
        logger.warning("detail read failed: %s", exc)
        refused_reason = str(exc)
        if (
            isinstance(exc, DetailSelectionRefused)
            and refused_reason != "detail_source_unavailable"
        ) or scoped.response_policy == "strict":
            return Incomplete(reason_code="detail_source_unavailable")
        failed_detail_families = tuple(
            sorted({selection.family for selection in scoped.plan.detail_selections})
        )
    finally:
        if writer is not None:
            writer.detail_read_ms = round(
                (time.perf_counter() - detail_started) * 1000,
                1,
            )
    if failed_detail_families and scoped.response_policy == "strict":
        return Incomplete(reason_code="detail_source_unavailable")

    return DetailReadOutcome(record_details, failed_detail_families)
