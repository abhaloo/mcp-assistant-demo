"""Execution helpers for InternalCompilerAdapter."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Select, Selectable

from app.auth import Principal
from app.business_query.authorize.scoping import ScopedDerivedSet, ScopedPlan
from app.business_query.compile.detail_engine import DetailEngine
from app.business_query.compile.execution import execute_detail_reads, is_statement_timeout
from app.business_query.compile.join_paths import row_identity_for
from app.business_query.compile.pagination.keyset import plan_is_pageable
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import (
    Denied,
    Incomplete,
    RecordDetail,
    RecordRef,
    RowIdentity,
)
from app.business_query.plan.filter_tree import PlanFilter, iter_filter_leaves
from app.business_query.plan.query_plan import BusinessQueryPlan, time_group_for
from app.business_query.seal.events import answer_query_id_for
from app.business_query.seal.evidence import (
    AdapterExecutionEvidence,
    UnsealedAdapterAnswer,
    UnsealedSelection,
    declared_result_members,
    normalize_event_rows,
    result_columns,
)
from app.business_query.seal.receipts import (
    RESERVED_SIDECAR_COLUMNS as _RESERVED_SIDECAR_COLUMNS,
)
from app.business_query.seal.receipts import (
    digest_json as _digest_json,
)
from app.business_query.seal.receipts import (
    split_record_refs as _split_record_refs_fn,
)
from app.business_query.wire.trace import QueryTrace
from app.rag.provenance.record_links import with_record_hrefs

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from app.business_query.compile.time_group_selection import SelectionRead

logger = logging.getLogger(__name__)

_DENY_MESSAGE = "business query tools are currently unavailable"


def split_record_refs(rows: list[dict[str, Any]]) -> tuple[RecordRef, ...]:
    return _split_record_refs_fn(rows)


def requested_anchor_count_for(plan: BusinessQueryPlan) -> int | None:
    """The limit of the pick derived set whose key is f"{plan.anchor}.id"
    and which is referenced by an in_set filter on that key; else None."""
    if plan.anchor is None or not plan.derived_sets:
        return None
    anchor_key = f"{plan.anchor}.id"
    referenced_ids: set[str] = set()
    for leaf in iter_filter_leaves(plan.filters):
        if isinstance(leaf, PlanFilter) and leaf.member == anchor_key and leaf.operator == "in_set":
            for val in leaf.values:
                referenced_ids.add(str(val))
    if not referenced_ids:
        return None
    for d in plan.derived_sets:
        if d.id in referenced_ids and d.mode == "pick" and d.key == anchor_key:
            limit = (
                d.plan.limit
                if hasattr(d.plan, "limit")
                else getattr(d.plan, "get", lambda _: None)("limit")
            )
            if isinstance(limit, int):
                return limit
    return None


@dataclass(frozen=True)
class _ExecutedRead:
    """The rows one statement returned, with the facts its evidence needs."""

    scoped: ScopedPlan
    compiled_sql: str
    compiled_params: dict[str, Any]
    rows: list[dict[str, Any]]
    keys: list[str]
    started_at: datetime
    finished_at: datetime
    total_row_count: int
    pageable: bool


def _execution_evidence(
    read: _ExecutedRead,
    *,
    bundle: DefinitionBundle,
    principal: Principal,
    database_identity: str,
    dialect_name: str,
) -> AdapterExecutionEvidence | Incomplete:
    member_names = [
        name
        for name in read.keys
        if name not in _RESERVED_SIDECAR_COLUMNS and not name.startswith("__bq_")
    ]
    try:
        members = declared_result_members(read.scoped.plan, bundle, member_names, read.rows)
        columns = result_columns(
            read.scoped.plan,
            bundle,
            members,
            display_members_added=read.scoped.display_members_added,
            principal=principal,
        )
    except (TypeError, ValueError):
        logger.warning("business query execute failed: result member contract mismatch")
        return Incomplete(reason_code="adapter_invalid")
    try:
        event_rows = normalize_event_rows(read.rows, members)
    except ValueError as exc:
        logger.warning(
            "result member contract mismatch member=%s bundle=%s",
            str(exc).split(":")[0],
            bundle.content_hash,
        )
        return Incomplete(reason_code="data_contract_mismatch")
    return AdapterExecutionEvidence(
        adapter="internal",
        backend=dialect_name,
        compiled_query_digest=hashlib.sha256(read.compiled_sql.encode()).hexdigest(),
        parameter_scope_digest=_digest_json(
            {
                "parameters": read.compiled_params,
                "forced": [predicate.model_dump(mode="json") for predicate in read.scoped.forced],
            }
        ),
        result_members=members,
        result_rows=event_rows,
        total_row_count=read.total_row_count,
        truncated=read.total_row_count > len(event_rows),
        database_identity=database_identity,
        started_at=read.started_at,
        finished_at=read.finished_at,
        result_columns=columns,
        pageable=read.pageable,
    )


def _reads_a_selection(scoped: ScopedPlan) -> bool:
    """True when a time-group set must be read before the answer statement is built."""
    return any(time_group_for(d.key, d.scoped.plan) is not None for d in scoped.derived)


def _selection_scoped(scoped: ScopedPlan, read: SelectionRead) -> ScopedPlan:
    """The selection's own scoped plan, with its own answer id for its evidence event."""
    if scoped.answer_query_id:
        selection_id = answer_query_id_for(f"{scoped.answer_query_id}:selection:{read.derived.id}")
    else:
        selection_id = uuid4().hex
    return read.derived.scoped.model_copy(update={"answer_query_id": selection_id})


def execute_internal_plan(
    scoped: ScopedPlan,
    *,
    engine: Engine,
    dialect: sa.engine.Dialect,
    dialect_name: str,
    bundle: DefinitionBundle,
    principal: Principal,
    database_identity: str | None,
    detail_adapter: DetailEngine,
    build_select_fn: Any,
    record_sql_fn: Any,
    trace: QueryTrace | None = None,
    writer_fn: Any,
    build_set_fn: Callable[[ScopedDerivedSet], Select[Any]] | None = None,
    resolve_fn: Callable[[Connection, ScopedPlan], tuple[ScopedPlan, tuple[SelectionRead, ...]]]
    | None = None,
) -> UnsealedAdapterAnswer | Incomplete | Denied:
    stmt: Selectable | None = None
    relation: Any = None
    started = time.perf_counter()
    started_at = datetime.now(tz=UTC)
    compiled_sql: str | None = None
    compiled_params: dict[str, Any] = {}
    selection_reads: tuple[SelectionRead, ...] = ()
    keys: list[str] = []
    rows: list[dict[str, Any]] = []
    identity = database_identity
    resolve = resolve_fn if resolve_fn is not None and _reads_a_selection(scoped) else None
    try:
        from app.business_query.authorize.scoping import ScopeDenied

        # A plan is built, and refused, before any database work; only a time-group
        # plan waits for its selection read to bind the periods it compares with.
        built: Any = None if resolve is not None else build_select_fn(scoped)
        if not identity:
            return Incomplete(reason_code="adapter_invalid")
        with engine.connect() as conn, conn.begin():
            if resolve is not None:
                try:
                    scoped, selection_reads = resolve(conn, scoped)
                except ValueError as exc:
                    # A selected period value the bucket reader cannot read.
                    logger.warning("business query resolve failed: %s", exc)
                    return Incomplete(reason_code="data_contract_mismatch")
                built = build_select_fn(scoped)
            if hasattr(built, "statement"):
                relation = built
                stmt = built.statement
            else:
                relation = getattr(built, "_relation", None)
                stmt = built
            compiled = stmt.compile(dialect=dialect)
            compiled_sql = str(compiled)
            compiled_params = dict(compiled.params)
            result = conn.execute(stmt)
            keys = list(result.keys())
            rows = [dict(zip(keys, row, strict=True)) for row in result.fetchall()]
            record_sql_fn(
                stmt,
                started,
                len(rows),
                trace,
                receipt_query_id=scoped.answer_query_id,
            )
    except ScopeDenied as exc:
        logger.warning("business query execute refused: %s", type(exc).__name__)
        return Denied(message=_DENY_MESSAGE, reason_code="policy_denied")
    except sa.exc.TimeoutError as exc:
        logger.warning("business query execute failed: %s", type(exc).__name__)
        if stmt is not None:
            record_sql_fn(
                stmt,
                started,
                rows=None,
                trace=trace,
                receipt_query_id=scoped.answer_query_id,
            )
        writer = writer_fn(trace)
        if writer is not None:
            writer.fail("execute", type(exc).__name__)
        return Incomplete(reason_code="timeout")
    except SQLAlchemyError as exc:
        detail = f"{type(exc).__name__}: {getattr(exc, 'orig', '')}"
        logger.warning("business query execute failed: %s", detail)
        if stmt is not None and is_statement_timeout(exc):
            record_sql_fn(
                stmt,
                started,
                rows=None,
                trace=trace,
                receipt_query_id=scoped.answer_query_id,
            )
        writer = writer_fn(trace)
        if writer is not None:
            writer.fail("execute", detail)
        if is_statement_timeout(exc):
            return Incomplete(reason_code="timeout")
        return Incomplete(reason_code="adapter_invalid")

    if scoped.plan.grain != "scalar":
        total_row_count = int(rows[0].pop("__bq_total_row_count")) if rows else 0
        for row in rows[1:]:
            row.pop("__bq_total_row_count", None)
    else:
        if rows and all(value is None for value in rows[0].values()):
            rows = []
        total_row_count = len(rows)

    empty_text = "No matching rows."
    no_period = next(
        (
            read
            for read in selection_reads
            if read.derived.resolved is not None and not read.derived.resolved.periods
        ),
        None,
    )
    if no_period is not None:
        empty_text = f"No matching rows. Set '{no_period.derived.id}' matched no period."
    unresolved = tuple(item for item in scoped.derived if item.resolved is None)
    if not rows and no_period is None and unresolved and build_set_fn is not None:
        witnessed = empty_stage_sentence(engine, unresolved, build_set_fn, record_sql_fn, trace)
        if isinstance(witnessed, Incomplete):
            writer = writer_fn(trace)
            if writer is not None:
                writer.fail("witness", witnessed.reason_code)
            return witnessed
        empty_text = witnessed

    record_refs = with_record_hrefs(split_record_refs(rows), rows, bundle, principal)

    assert compiled_sql is not None
    finished_at = datetime.now(tz=UTC)
    evidence_or_error = _execution_evidence(
        _ExecutedRead(
            scoped=scoped,
            compiled_sql=compiled_sql,
            compiled_params=compiled_params,
            rows=rows,
            keys=keys,
            started_at=started_at,
            finished_at=finished_at,
            total_row_count=total_row_count,
            pageable=plan_is_pageable(scoped.plan, bundle),
        ),
        bundle=bundle,
        principal=principal,
        database_identity=identity,
        dialect_name=dialect_name,
    )
    if isinstance(evidence_or_error, Incomplete):
        return evidence_or_error
    evidence = evidence_or_error
    event_rows = list(evidence.result_rows)
    answer_text = f"Returned {len(rows)} row(s)." if rows else empty_text
    record_details: list[RecordDetail] = []
    failed_detail_families: tuple[str, ...] = ()
    if scoped.plan.detail_selections and rows:
        outcome = execute_detail_reads(
            detail_adapter=detail_adapter,
            bundle=bundle,
            scoped=scoped,
            rows=rows,
            record_refs=record_refs,
            principal=principal,
            trace=trace,
            writer=writer_fn(trace),
        )
        if isinstance(outcome, (Denied, Incomplete)):
            return outcome
        record_details = outcome.record_details
        failed_detail_families = outcome.failed_detail_families

    row_identity: RowIdentity | None = None
    if scoped.plan.anchor is not None:
        anchor = relation.anchor if relation and relation.anchor else scoped.plan.anchor
        expanded = relation.expanded if relation else None
        # Without a record id for the anchor there is nothing to count, so no
        # identity line is claimed.
        if any(ref.resource == anchor for ref in record_refs):
            row_identity = row_identity_for(
                record_refs,
                anchor=anchor,
                expanded=expanded,
                row_count=len(rows),
                requested_anchor_count=requested_anchor_count_for(scoped.plan),
            )

    selections: list[UnsealedSelection] = []
    for read in selection_reads:
        selection_scoped = _selection_scoped(scoped, read)
        record_sql_fn(
            read.statement,
            started,
            len(read.rows),
            trace,
            receipt_query_id=selection_scoped.answer_query_id,
        )
        selection_rows = list(read.rows)
        if selection_rows:
            selection_keys = list(selection_rows[0].keys())
        else:
            selection_keys = list(read.statement.exported_columns.keys())
        selection_compiled = read.statement.compile(dialect=dialect)
        selection_evidence = _execution_evidence(
            _ExecutedRead(
                scoped=selection_scoped,
                compiled_sql=str(selection_compiled),
                compiled_params=dict(selection_compiled.params),
                rows=selection_rows,
                keys=selection_keys,
                started_at=started_at,
                finished_at=datetime.now(tz=UTC),
                total_row_count=len(selection_rows),
                pageable=False,
            ),
            bundle=bundle,
            principal=principal,
            database_identity=identity,
            dialect_name=dialect_name,
        )
        if isinstance(selection_evidence, Incomplete):
            return selection_evidence
        selections.append(
            UnsealedSelection(
                scoped=selection_scoped,
                answer=UnsealedAdapterAnswer(
                    answer_text=f"Selected {len(selection_rows)} period(s).",
                    rows=list(selection_evidence.result_rows),
                    total_row_count=len(selection_rows),
                    evidence=selection_evidence,
                ),
            )
        )

    return UnsealedAdapterAnswer(
        answer_text=answer_text,
        rows=list(event_rows),
        total_row_count=total_row_count,
        evidence=evidence,
        record_refs=record_refs,
        record_details=record_details,
        failed_detail_families=failed_detail_families,
        row_identity=row_identity,
        selections=tuple(selections),
    )


def empty_stage_sentence(
    engine: Engine,
    derived: tuple[ScopedDerivedSet, ...],
    build_set_fn: Callable[[ScopedDerivedSet], Select[Any]],
    record_sql_fn: Any,
    trace: Any,
) -> str | Incomplete:
    """Which stage an empty answer emptied at, from one key select per declared set.

    Runs only after the answer itself came back empty, so it sees a later instant than
    the answer statement; the text is diagnostic, not evidence, and mints no record
    references. A failing witness is an Incomplete: a sentence that names no stage
    would say less than the code knows.
    """
    last_id = ""
    last_keys: list[Any] = []
    last_mode = "complete"
    for item in derived:
        if item.resolved is not None:
            continue
        relation = build_set_fn(item)
        started = time.perf_counter()
        try:
            with engine.connect() as conn:
                keys = [row[0] for row in conn.execute(relation).fetchall()]
        except SQLAlchemyError as exc:
            logger.warning("empty-stage witness failed: %s", type(exc).__name__)
            return Incomplete(reason_code="adapter_invalid")
        record_sql_fn(relation, started, len(keys), trace, receipt_query_id=None)
        if not keys:
            return f"No matching rows. Set '{item.id}' matched nothing."
        last_id, last_keys, last_mode = item.id, keys, item.mode
    if last_mode == "complete":
        return (
            f"No matching rows. Set '{last_id}' matched {len(last_keys)} keys; "
            "the answer plan matched none of them."
        )
    listed = ", ".join(str(key) for key in last_keys)
    key_name = next(item.key for item in derived if item.id == last_id)
    return (
        f"No matching rows. Set '{last_id}' matched {key_name} {listed}; "
        "the answer plan matched none of them."
    )
