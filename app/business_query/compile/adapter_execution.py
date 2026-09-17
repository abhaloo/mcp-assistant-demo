"""Execution helpers for InternalCompilerAdapter."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Selectable

from app.auth import Principal
from app.business_query.authorize.scoping import ScopedPlan
from app.business_query.compile.detail_engine import DetailEngine
from app.business_query.compile.execution import execute_detail_reads, is_statement_timeout
from app.business_query.compile.pagination.keyset import plan_is_pageable
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import (
    Denied,
    Incomplete,
    RecordDetail,
    RecordRef,
)
from app.business_query.seal.evidence import (
    AdapterExecutionEvidence,
    UnsealedAdapterAnswer,
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

logger = logging.getLogger(__name__)

_DENY_MESSAGE = "business query tools are currently unavailable"


def split_record_refs(rows: list[dict[str, Any]]) -> tuple[RecordRef, ...]:
    return _split_record_refs_fn(rows)


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
) -> UnsealedAdapterAnswer | Incomplete | Denied:
    stmt: Selectable | None = None
    started = time.perf_counter()
    started_at = datetime.now(tz=UTC)
    compiled_sql: str | None = None
    compiled_params: dict[str, Any] = {}
    try:
        from app.business_query.authorize.scoping import ScopeDenied

        stmt = build_select_fn(scoped)
    except ScopeDenied as exc:
        logger.warning("business query execute refused: %s", type(exc).__name__)
        return Denied(message=_DENY_MESSAGE, reason_code="policy_denied")

    identity = database_identity
    if not identity:
        return Incomplete(reason_code="adapter_invalid")

    try:
        compiled = stmt.compile(dialect=dialect)
        compiled_sql = str(compiled)
        compiled_params = dict(compiled.params)
        with engine.connect() as conn:
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

    record_refs = split_record_refs(rows)

    assert compiled_sql is not None
    finished_at = datetime.now(tz=UTC)
    member_names = [name for name in result.keys() if name not in _RESERVED_SIDECAR_COLUMNS]
    try:
        members = declared_result_members(scoped.plan, bundle, member_names, rows)
        columns = result_columns(scoped.plan, bundle, members)
        event_rows = normalize_event_rows(rows, members)
    except (TypeError, ValueError):
        logger.warning("business query execute failed: result member contract mismatch")
        return Incomplete(reason_code="adapter_invalid")
    evidence = AdapterExecutionEvidence(
        adapter="internal",
        backend=dialect_name,
        compiled_query_digest=hashlib.sha256(compiled_sql.encode()).hexdigest(),
        parameter_scope_digest=_digest_json(
            {
                "parameters": compiled_params,
                "forced": [predicate.model_dump(mode="json") for predicate in scoped.forced],
            }
        ),
        result_members=members,
        result_rows=event_rows,
        total_row_count=total_row_count,
        truncated=total_row_count > len(event_rows),
        database_identity=identity,
        started_at=started_at,
        finished_at=finished_at,
        result_columns=columns,
        pageable=plan_is_pageable(scoped.plan, bundle),
    )
    answer_text = f"Returned {len(rows)} row(s)." if rows else "No matching rows."
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

    return UnsealedAdapterAnswer(
        answer_text=answer_text,
        rows=list(event_rows),
        total_row_count=total_row_count,
        evidence=evidence,
        record_refs=record_refs,
        record_details=record_details,
        failed_detail_families=failed_detail_families,
    )
