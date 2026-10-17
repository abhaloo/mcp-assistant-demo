"""Matched-MariaDB baseline capture for the Q1 query-performance gate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from app.auth import Principal
from app.auth.record_access import ScopeValues
from app.business_query.authorize.scoping import apply_role_scope
from app.business_query.compile.adapter import InternalCompilerAdapter
from app.business_query.compile.execution import is_statement_timeout
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Answered, Incomplete
from app.business_query.plan import BusinessQueryPlan
from app.business_query.seal.evidence import seal_adapter_result
from app.business_query.wire.module import UnsealedAdapterAnswer
from app.business_query.wire.trace import QueryTrace
from app.experiments.bq_query_performance.benchmark import (
    REPO_ROOT,
    BenchmarkNotRunnable,
    BenchmarkPerformanceFailure,
    BenchmarkPreflightError,
    _assert_frozen_execute_identity,
    _shape,
    load_benchmark_contract,
    nearest_rank_p95,
    write_benchmark_artifact,
)

_MARIADB_DIALECTS = frozenset({"mysql", "mariadb"})
_PRODUCT_TIMEOUT_SECONDS = 10.0
_DIAGNOSTIC_TIMEOUT_SECONDS = 120.0
_BILL_FACT_VIEW = "ai_v1_bq_bill_fact"
_INDEX_TABLES = ("bills", "bill_items", "journals", "accounts", "customers", "currencies")
_CONFIG_VARS = (
    "innodb_buffer_pool_size",
    "optimizer_switch",
    "sql_mode",
    "tmp_table_size",
    "max_heap_table_size",
)
_VIEW_TABLE_SCHEMA_RE = re.compile(
    r"(?:`([A-Za-z0-9_]+)`|([A-Za-z0-9_]+))\."
    r"(?:`?(?:bills|bill_items|journals|accounts|customers|currencies)`?)",
    re.IGNORECASE,
)
_MARIADB_ENTITY_ID = 1
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "data" / "bq-query-performance" / "baseline"
_ARM_FINGERPRINT_FIELDS = (
    "compiled_query_digest",
    "environment_fingerprint",
    "index_fingerprint",
    "mariadb_version",
    "mariadb_config",
)


def assert_capture_matches_freeze(contract: Mapping[str, Any], live: Mapping[str, Any]) -> None:
    """Refuse a drifted snapshot after the matched-MariaDB freeze is set."""
    frozen_identity = contract.get("matched_dataset_identity")
    if not frozen_identity:
        return
    contract_declared = contract.get("declared_causal_difference")
    live_declared = live.get("declared_causal_difference")
    if live_declared not in (None, "") and live_declared != contract_declared:
        raise BenchmarkNotRunnable("matched MariaDB freeze drifted: declared_causal_difference")
    live_identity = live.get("matched_dataset_identity")
    view_hash_changed = _view_hash_changed(
        frozen_identity,
        live_identity,
        contract_declared,
    )
    if view_hash_changed:
        _require_freeze_equal(
            "matched_dataset_identity.without_view_sha256",
            _identity_without_view(str(frozen_identity)),
            _identity_without_view(str(live_identity)),
        )
    else:
        _require_freeze_equal("matched_dataset_identity", frozen_identity, live_identity)
    frozen_digests = contract.get("shape_compiled_query_digests") or {}
    live_digests = live.get("shape_compiled_query_digests")
    if live_digests is not None:
        _require_freeze_equal("shape_compiled_query_digests", frozen_digests, live_digests)
    elif live.get("shape_id") and frozen_digests and not view_hash_changed:
        shape_id = str(live["shape_id"])
        _require_freeze_equal(
            f"shape_compiled_query_digests.{shape_id}",
            frozen_digests.get(shape_id),
            live.get("compiled_query_digest"),
        )
    live_arms = live.get("arms")
    frozen_arms = contract.get("arms") or {}
    if live_arms:
        for arm_name, frozen_arm in frozen_arms.items():
            live_arm = (live_arms or {}).get(arm_name) or {}
            for field in _ARM_FINGERPRINT_FIELDS:
                if (
                    arm_name == "candidate"
                    and field == "compiled_query_digest"
                    and view_hash_changed
                ):
                    continue
                _require_freeze_equal(
                    f"arms.{arm_name}.{field}", frozen_arm.get(field), live_arm.get(field)
                )
    else:
        frozen_arm = frozen_arms.get("baseline") or {}
        for field in (
            "environment_fingerprint",
            "index_fingerprint",
            "mariadb_version",
            "mariadb_config",
        ):
            if frozen_arm.get(field) is not None:
                _require_freeze_equal(field, frozen_arm.get(field), live.get(field))
    frozen_results = contract.get("mariadb_result_identity") or {}
    captures = live.get("captures")
    if captures:
        for shape_id, frozen in frozen_results.items():
            if _skip_null_overdue_result_identity(shape_id, frozen, view_hash_changed):
                continue
            _assert_result_identity_matches(shape_id, frozen, captures.get(shape_id) or {})
    elif live.get("shape_id") in frozen_results:
        shape_id = str(live["shape_id"])
        frozen = frozen_results[shape_id]
        if not _skip_null_overdue_result_identity(shape_id, frozen, view_hash_changed):
            _assert_result_identity_matches(shape_id, frozen, live)


def _require_freeze_equal(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise BenchmarkNotRunnable(f"matched MariaDB freeze drifted: {label}")


def _view_sha256(identity: str | None) -> str | None:
    if not identity or "view_sha256=" not in identity:
        return None
    return identity.split("view_sha256=", 1)[1]


def _identity_without_view(identity: str) -> str:
    return "|".join(part for part in identity.split("|") if not part.startswith("view_sha256="))


def _view_hash_changed(frozen_identity: Any, live_identity: Any, declared: Any) -> bool:
    if declared != "semantic_view_shape" or not frozen_identity or not live_identity:
        return False
    frozen = str(frozen_identity)
    live = str(live_identity)
    return _view_sha256(frozen) != _view_sha256(live) and _identity_without_view(
        frozen
    ) == _identity_without_view(live)


def _skip_null_overdue_result_identity(
    shape_id: str, frozen: Mapping[str, Any], view_hash_changed: bool
) -> bool:
    return (
        view_hash_changed and shape_id == "overdue_outstanding" and frozen.get("row_sha256") is None
    )


def _arm_from_identity(identity: Mapping[str, Any], compiled_query_digest: str) -> dict[str, Any]:
    return {
        "compiled_query_digest": compiled_query_digest,
        "environment_fingerprint": identity["environment_fingerprint"],
        "index_fingerprint": identity["index_fingerprint"],
        "mariadb_version": identity["mariadb_version"],
        "mariadb_config": identity["mariadb_config"],
    }


def compose_capture_result(
    contract: Mapping[str, Any],
    captures: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep frozen baseline fingerprints; label live capture as the candidate arm."""
    shape_ids = list(captures)
    overdue = captures["overdue_outstanding"]
    live_arm = _arm_from_identity(identity, str(overdue["compiled_query_digest"]))
    frozen_baseline = dict((contract.get("arms") or {}).get("baseline") or live_arm)
    declared = contract.get("declared_causal_difference")
    return {
        "matched_dataset_identity": identity["matched_dataset_identity"],
        "shape_ids": shape_ids,
        "captures": dict(captures),
        "shape_compiled_query_digests": {
            shape_id: captures[shape_id]["compiled_query_digest"] for shape_id in shape_ids
        },
        "arms": {"baseline": frozen_baseline, "candidate": live_arm},
        "declared_causal_difference": declared if declared else None,
    }


def residual_index_required(
    *,
    timeout: bool,
    duration_seconds: float,
    hot_p95_seconds: float,
    concurrent_p95_seconds: float,
    concurrent_requests: int,
) -> bool:
    """True when one product-timeout sample would miss D-Q1-BUDGET or the concurrent gate."""
    if timeout:
        return True
    if duration_seconds >= hot_p95_seconds:
        return True
    return duration_seconds * concurrent_requests >= concurrent_p95_seconds


def journals_derived_scan(explain_json: Any) -> dict[str, Any] | None:
    """Name the journals operator inside a DERIVED grouping, if present."""
    found: dict[str, Any] | None = None

    def walk(node: Any, using_temporary_table: bool = False) -> None:
        nonlocal found
        if found is not None:
            return
        if isinstance(node, dict):
            grouping = node.get("grouping_operation")
            temp = using_temporary_table
            if isinstance(grouping, dict) and grouping.get("using_temporary_table"):
                temp = True
            table_name = str(node.get("table_name") or node.get("table") or "")
            bare = table_name.rsplit(".", 1)[-1].strip("`")
            if bare.lower() == "journals":
                rows = node.get("rows")
                found = {
                    "table": bare,
                    "access_type": str(node.get("access_type") or ""),
                    "rows": int(rows) if isinstance(rows, (int, float)) else None,
                    "using_temporary_table": temp,
                }
                return
            for value in node.values():
                walk(value, temp)
        elif isinstance(node, list):
            for item in node:
                walk(item, using_temporary_table)

    walk(explain_json)
    return found


def has_correlated_per_bill_aggregates(
    select_types: list[str], explain_json: Any | None = None
) -> bool:
    """True when EXPLAIN still has the eight DEPENDENT SUBQUERY journals-ALL shape."""
    dependent = sum(1 for item in select_types if str(item).upper() == "DEPENDENT SUBQUERY")
    if dependent >= 8:
        return True
    if explain_json is None:
        return False
    return _journals_all_attached_to_bills_id(explain_json)


def _journals_all_attached_to_bills_id(node: Any) -> bool:
    if isinstance(node, dict):
        table = str(node.get("table") or "")
        access = str(node.get("access_type") or "")
        attached = str(node.get("attached_condition") or "").replace("`", "")
        if (
            table.lower().endswith("journals")
            and access.upper() == "ALL"
            and "bill_id" in attached
            and "bills.id" in attached
        ):
            return True
        return any(_journals_all_attached_to_bills_id(value) for value in node.values())
    if isinstance(node, list):
        return any(_journals_all_attached_to_bills_id(item) for item in node)
    return False


def judge_concurrent_load(
    contract: Mapping[str, Any],
    *,
    durations: list[float],
    timeouts: int,
    single_request_seconds: float,
) -> float:
    """Score 5×10 concurrent durations against the frozen load gate. Does not retune D-Q1-BUDGET."""
    mapping = dict(contract)
    load = mapping["load"]
    expected = int(load["concurrent_target_requests"]) * int(load["rounds"])
    if len(durations) != expected:
        raise BenchmarkPreflightError(
            f"concurrent sample count {len(durations)} drifted from load protocol {expected}"
        )
    if timeouts:
        raise BenchmarkPerformanceFailure(
            f"{timeouts} product timeouts under concurrent load; D-Q1-BUDGET requires zero"
        )
    p95 = nearest_rank_p95(durations)
    limit = float(load["concurrent_p95_seconds"])
    if p95 > limit:
        raise BenchmarkPerformanceFailure(
            f"concurrent p95 {p95} exceeded load gate {limit} seconds"
        )
    hot = float(mapping["budget"]["hot_shape_p95_seconds"])
    if single_request_seconds > hot:
        raise BenchmarkPerformanceFailure(
            f"single-request {single_request_seconds} exceeded D-Q1-BUDGET {hot} seconds"
        )
    return p95


def _load_gate_answered(raw: Any) -> bool:
    return isinstance(raw, (UnsealedAdapterAnswer, Answered))


def _refuse_load_non_answer(raw: Any, *, site: str) -> None:
    if _load_gate_answered(raw):
        return
    if isinstance(raw, Incomplete) and raw.reason_code == "timeout":
        raise BenchmarkPerformanceFailure(f"{site} timed out under the product 10s cap")
    kind = type(raw).__name__
    detail = getattr(raw, "reason_code", None) or getattr(raw, "outcome", kind)
    raise BenchmarkPerformanceFailure(f"{site} did not answer: {detail}")


def run_session_timeout_victim(
    adapter: InternalCompilerAdapter,
    *,
    sleep_seconds: float = 2.0,
    statement_timeout_seconds: float = 0.3,
) -> dict[str, Any]:
    """Labeled diagnostic: MariaDB max_statement_time kills SLEEP; no second execute.

    Does not change the adapter's restored product timeout default.
    """
    if adapter.dialect_name not in _MARIADB_DIALECTS:
        raise BenchmarkPreflightError("timeout victim requires MariaDB")
    if statement_timeout_seconds >= sleep_seconds:
        raise BenchmarkPreflightError("victim sleep must exceed the statement timeout")
    product_timeout = adapter._statement_timeout_seconds
    engine = adapter._engine
    diagnostic_engine = engine
    owns_diagnostic_engine = False
    # The diagnostic deliberately uses a separate engine whose pooled
    # connections are initialized with its short ceiling.  Production keeps
    # its fixed 10s initialization on the shared composition engine; this
    # path must not mutate a live pooled session per request.
    if isinstance(engine, sa.engine.Engine) and statement_timeout_seconds != product_timeout:
        diagnostic_engine = sa.create_engine(
            engine.url,
            pool_pre_ping=True,
            connect_args={
                "init_command": (
                    f"SET SESSION max_statement_time = {float(statement_timeout_seconds)}"
                )
            },
        )
        owns_diagnostic_engine = True
    select_execute_count = 0
    timed_out = False
    started = time.perf_counter()
    try:
        adapter._statement_timeout_seconds = statement_timeout_seconds
        with diagnostic_engine.connect() as conn:
            sleep_sql = f"SELECT SLEEP({float(sleep_seconds)})"
            select_execute_count += 1
            try:
                conn.exec_driver_sql(sleep_sql)
            except SQLAlchemyError as exc:
                if is_statement_timeout(exc):
                    timed_out = True
                else:
                    raise
        duration = round(time.perf_counter() - started, 4)
        leftover = _sleep_process_ids(diagnostic_engine)
        if leftover:
            raise BenchmarkNotRunnable("SLEEP still running after session timeout")
        if select_execute_count != 1:
            raise BenchmarkNotRunnable(f"timeout victim retried execute: {select_execute_count}")
        return {
            "label": "timeout_victim_sleep",
            "timeout": timed_out,
            "reason_code": "timeout" if timed_out else None,
            "duration_seconds": duration,
            "statement_timeout_seconds": statement_timeout_seconds,
            "sleep_seconds": sleep_seconds,
            "select_execute_count": select_execute_count,
            "leftover_sleep_processes": 0,
            "product_timeout_seconds": product_timeout,
        }
    finally:
        adapter._statement_timeout_seconds = product_timeout
        if owns_diagnostic_engine:
            diagnostic_engine.dispose()


def _sleep_process_ids(engine: sa.Engine) -> list[int]:
    with engine.connect() as probe:
        rows = probe.execute(
            sa.text(
                "SELECT ID FROM information_schema.PROCESSLIST "
                "WHERE INFO LIKE '%SLEEP(%' AND INFO NOT LIKE '%PROCESSLIST%'"
            )
        ).fetchall()
    return [int(row[0]) for row in rows]


def run_concurrent_load_gate(
    adapter: InternalCompilerAdapter,
    principal: Principal,
    bundle: DefinitionBundle,
    contract: Mapping[str, Any],
    shape_id: str = "overdue_outstanding",
    *,
    business_date: date | None = None,
) -> dict[str, Any]:
    """5 concurrent target requests × 10 rounds on pool_size=5 / max_overflow=0."""
    engine = adapter._engine
    pool = engine.pool
    pool_size = int(pool.size())
    max_overflow = int(getattr(pool, "_max_overflow", -1))
    if pool_size != 5 or max_overflow != 0:
        raise BenchmarkPreflightError(
            f"load gate requires pool_size=5 and max_overflow=0, got {pool_size}/{max_overflow}"
        )
    if adapter._statement_timeout_seconds != _PRODUCT_TIMEOUT_SECONDS:
        raise BenchmarkPreflightError("load gate must use the product 10s statement cap")
    if adapter._trace is not None:
        raise BenchmarkPreflightError("load gate requires adapter._trace is None")
    execute_contract = _overlay_mariadb_principal(contract, principal)
    frozen_date = _assert_frozen_execute_identity(
        principal, bundle, execute_contract, business_date
    )
    shape = _shape(execute_contract, shape_id)
    plan = BusinessQueryPlan.model_validate(shape["plan"])
    scoped = apply_role_scope(plan, principal, bundle, business_date=frozen_date)
    local = threading.local()

    def on_checkout(dbapi_conn: Any, connection_record: Any, connection_proxy: Any) -> None:
        start = getattr(local, "request_start", None)
        if start is not None and getattr(local, "pool_wait", None) is None:
            local.pool_wait = time.perf_counter() - start

    def on_before_execute(
        conn: Any,
        cursor: Any,
        statement: Any,
        parameters: Any,
        context: Any,
        executemany: Any,
    ) -> None:
        start = getattr(local, "request_start", None)
        if start is None or getattr(local, "execute_start", None) is not None:
            return
        local.execute_start = time.perf_counter()
        if getattr(local, "pool_wait", None) is None:
            local.pool_wait = local.execute_start - start

    sa.event.listen(engine, "checkout", on_checkout)
    sa.event.listen(engine, "before_cursor_execute", on_before_execute)
    samples: list[dict[str, Any]] = []
    try:
        serial_started = time.perf_counter()
        serial_trace = QueryTrace(correlation_id="q1-serial")
        serial_raw = adapter.execute_with_evidence(scoped, trace=serial_trace)
        single_request_seconds = round(time.perf_counter() - serial_started, 4)
        _refuse_load_non_answer(serial_raw, site="serial target")

        load = dict(contract["load"])
        workers = int(load["concurrent_target_requests"])
        rounds = int(load["rounds"])

        def one_request(correlation_id: str) -> dict[str, Any]:
            local.request_start = time.perf_counter()
            local.pool_wait = None
            local.execute_start = None
            trace = QueryTrace(correlation_id=correlation_id)
            raw = adapter.execute_with_evidence(scoped, trace=trace)
            duration = round(time.perf_counter() - local.request_start, 4)
            if _load_gate_answered(raw):
                timed_out = False
            elif isinstance(raw, Incomplete) and raw.reason_code == "timeout":
                timed_out = True
            else:
                _refuse_load_non_answer(raw, site=f"concurrent {correlation_id}")
                timed_out = False
            return {
                "correlation_id": trace.correlation_id,
                "trace_id": id(trace),
                "trace": trace,
                "duration_seconds": duration,
                "timeout": timed_out,
                "pool_wait_seconds": float(local.pool_wait or 0.0),
                "sql_ms": trace.sql_ms,
            }

        for round_index in range(rounds):
            with ThreadPoolExecutor(max_workers=workers) as pool_exec:
                futures = [
                    pool_exec.submit(one_request, f"q1-load-{round_index}-{slot}")
                    for slot in range(workers)
                ]
                for future in as_completed(futures):
                    samples.append(future.result())
    finally:
        sa.event.remove(engine, "before_cursor_execute", on_before_execute)
        sa.event.remove(engine, "checkout", on_checkout)

    durations = [item["duration_seconds"] for item in samples]
    timeouts = sum(1 for item in samples if item["timeout"])
    p95 = judge_concurrent_load(
        contract,
        durations=durations,
        timeouts=timeouts,
        single_request_seconds=single_request_seconds,
    )
    return {
        "shape_id": shape_id,
        "durations": durations,
        "timeouts": timeouts,
        "samples": samples,
        "single_request_seconds": single_request_seconds,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "product_timeout_seconds": _PRODUCT_TIMEOUT_SECONDS,
        "p95_seconds": p95,
    }


def _assert_result_identity_matches(
    shape_id: str, frozen: Mapping[str, Any], payload: Mapping[str, Any]
) -> None:
    mariadb = payload.get("mariadb") or {}
    _require_freeze_equal(
        f"mariadb_result_identity.{shape_id}.member_schema",
        frozen.get("member_schema"),
        payload.get("member_schema"),
    )
    _require_freeze_equal(
        f"mariadb_result_identity.{shape_id}.row_sha256",
        frozen.get("row_sha256"),
        payload.get("result_identity"),
    )
    _require_freeze_equal(
        f"mariadb_result_identity.{shape_id}.product_timeout",
        frozen.get("product_timeout"),
        mariadb.get("timeout"),
    )
    _require_freeze_equal(
        f"mariadb_result_identity.{shape_id}.diagnostic_timeout",
        frozen.get("diagnostic_timeout"),
        mariadb.get("diagnostic_timeout"),
    )


def capture_mariadb_baseline(*, artifact_dir: Path | None = None) -> dict[str, Any]:
    """Capture matched-MariaDB identity, EXPLAIN, and one product-timeout execute.

    Refuses a performance pass when the record-database URL is missing.
    """
    url = os.environ.get("MCP_RECORD_DATABASE_URL")
    if not url:
        raise BenchmarkNotRunnable(
            "matched MariaDB URL is required; SQLite timings are not performance evidence"
        )
    artifact_dir = artifact_dir or DEFAULT_ARTIFACT_DIR
    engine = sa.create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception:
        engine.dispose()
        raise BenchmarkNotRunnable("matched MariaDB is unreachable") from None
    try:
        contract = load_benchmark_contract()
        fixture = REPO_ROOT / contract["bundle"]["fixture"]
        bundle = DefinitionBundle.model_validate_json(fixture.read_text(encoding="utf-8"))
        frozen = contract["principal"]
        principal = Principal(
            user_id="q1-benchmark",
            role="sales",
            permissions=list(frozen["permissions"]),
            entity_id=_MARIADB_ENTITY_ID,
            cross_entity=bool(frozen.get("cross_entity")),
            department_id=frozen.get("department_id"),
            scope_values=ScopeValues(department_id=frozen.get("department_id")),
        )
        adapter = InternalCompilerAdapter(
            principal=principal,
            engine=engine,
            bundle=bundle,
            statement_timeout_seconds=_PRODUCT_TIMEOUT_SECONDS,
            database_identity="pending-matched-mariadb",
        )
        business_date = date.fromisoformat(str(contract["business_date"]))
        shape_ids = [shape["id"] for shape in contract["shapes"]]
        captures: dict[str, Any] = {}
        for shape_id in shape_ids:
            captures[shape_id] = capture_shape_baseline(
                adapter,
                principal,
                bundle,
                contract,
                shape_id,
                business_date=business_date,
                artifact_path=_write_once_path(artifact_dir, shape_id),
            )
        first = captures[shape_ids[0]]
        result = compose_capture_result(contract, captures, first)
        assert_capture_matches_freeze(contract, result)
        return result
    finally:
        engine.dispose()


def capture_shape_baseline(
    adapter: InternalCompilerAdapter,
    principal: Principal,
    bundle: DefinitionBundle,
    contract: Mapping[str, Any],
    shape_id: str,
    *,
    business_date: date | None = None,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    """Capture EXPLAIN, timeout, and duration for one frozen shape on MariaDB."""
    dialect = adapter.dialect_name
    if dialect not in _MARIADB_DIALECTS:
        raise BenchmarkPreflightError(
            "SQLite timings are not performance evidence; MariaDB EXPLAIN/timeout/"
            f"duration require a MariaDB backend, got {dialect}"
        )
    engine = adapter._engine
    identity = read_matched_dataset_identity(engine)
    adapter._database_identity = identity["matched_dataset_identity"]
    execute_contract = _overlay_mariadb_principal(contract, principal)
    frozen_date = _assert_frozen_execute_identity(
        principal, bundle, execute_contract, business_date
    )
    shape = _shape(execute_contract, shape_id)
    plan = BusinessQueryPlan.model_validate(shape["plan"])
    scoped = apply_role_scope(plan, principal, bundle, business_date=frozen_date)
    compiled_sql = adapter.compile_sql(scoped)
    digest = hashlib.sha256(compiled_sql.encode()).hexdigest()
    explain_json, select_types = _explain_plan(engine, adapter, scoped)

    started = time.perf_counter()
    raw = adapter.execute_with_evidence(scoped)
    duration = round(time.perf_counter() - started, 4)
    timed_out = isinstance(raw, Incomplete) and raw.reason_code == "timeout"

    diagnostic: dict[str, Any] | None = None
    if timed_out:
        diagnostic = _diagnostic_execute(
            engine, principal, bundle, scoped, identity["matched_dataset_identity"]
        )
        raw = diagnostic["raw"]

    payload = _shape_payload(
        shape_id=shape_id,
        digest=digest,
        identity=identity,
        explain_json=explain_json,
        select_types=select_types,
        duration=duration,
        timed_out=timed_out,
        raw=raw,
        scoped=scoped,
        principal=principal,
        bundle=bundle,
        diagnostic=diagnostic,
        declared_causal_difference=execute_contract.get("declared_causal_difference"),
    )
    assert_capture_matches_freeze(contract, payload)
    if artifact_path is not None:
        write_benchmark_artifact(artifact_path, payload)
    return payload


def _write_once_path(directory: Path, shape_id: str) -> Path:
    path = directory / f"{shape_id}.json"
    if not path.exists():
        return path
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"{shape_id}-{stamp}.json"


def read_matched_dataset_identity(engine: sa.Engine) -> dict[str, Any]:
    """Database name, server version, and snapshot content identity."""
    with engine.connect() as conn:
        database = conn.execute(sa.text("SELECT DATABASE()")).scalar()
        version = conn.execute(sa.text("SELECT VERSION()")).scalar()
        bills = conn.execute(
            sa.text("SELECT COUNT(*) FROM bills WHERE entity_id = :entity_id"),
            {"entity_id": _MARIADB_ENTITY_ID},
        ).scalar()
        create_row = conn.execute(sa.text(f"SHOW CREATE VIEW {_BILL_FACT_VIEW}")).fetchone()
        config = {name: conn.execute(sa.text(f"SELECT @@{name}")).scalar() for name in _CONFIG_VARS}
        if not database or not version or create_row is None:
            raise BenchmarkNotRunnable("matched MariaDB identity could not be read")
        view_sql = str(create_row[1])
        schemas_sql = ", ".join(
            f"'{name}'" for name in _index_schemas(conn, str(database), view_sql)
        )
        tables_sql = ", ".join(f"'{name}'" for name in _INDEX_TABLES)
        index_rows = conn.execute(
            sa.text(
                "SELECT TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, "
                "NON_UNIQUE FROM information_schema.STATISTICS "
                f"WHERE TABLE_SCHEMA IN ({schemas_sql}) AND TABLE_NAME IN ({tables_sql}) "
                "ORDER BY TABLE_SCHEMA, TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
            )
        ).fetchall()
    view_sha = hashlib.sha256(view_sql.encode("utf-8")).hexdigest()
    identity = f"{database}|{version}|bills_entity1={int(bills)}|view_sha256={view_sha}"
    index_payload = [
        {
            "schema": row[0],
            "table": row[1],
            "index": row[2],
            "seq": int(row[3]),
            "column": row[4],
            "non_unique": int(row[5]),
        }
        for row in index_rows
    ]
    config_json = json.dumps(config, sort_keys=True, default=str)
    index_json = json.dumps(index_payload, sort_keys=True)
    return {
        "matched_dataset_identity": identity,
        "mariadb_version": str(version),
        "mariadb_config": hashlib.sha256(config_json.encode()).hexdigest(),
        "mariadb_config_values": config,
        "environment_fingerprint": hashlib.sha256(f"{version}|{config_json}".encode()).hexdigest(),
        "index_fingerprint": hashlib.sha256(index_json.encode()).hexdigest(),
        "index_inventory": index_payload,
        "bills_entity1": int(bills),
        "view_sha256": view_sha,
    }


def _index_schemas(conn: sa.Connection, database: str, view_sql: str) -> tuple[str, ...]:
    schemas = {database}
    for match in _VIEW_TABLE_SCHEMA_RE.finditer(view_sql):
        schemas.add(match.group(1) or match.group(2))
    tables_sql = ", ".join(f"'{name}'" for name in _INDEX_TABLES)
    local_views = conn.execute(
        sa.text(
            "SELECT VIEW_DEFINITION FROM information_schema.VIEWS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({tables_sql})"
        )
    ).fetchall()
    for (definition,) in local_views:
        for match in _VIEW_TABLE_SCHEMA_RE.finditer(str(definition)):
            schemas.add(match.group(1) or match.group(2))
    return tuple(sorted(schemas))


def _overlay_mariadb_principal(contract: Mapping[str, Any], principal: Principal) -> dict[str, Any]:
    overlaid = json.loads(json.dumps(dict(contract)))
    overlaid["principal"] = dict(overlaid.get("principal") or {})
    overlaid["principal"]["entity_id"] = principal.entity_id
    return overlaid


def _explain_plan(
    engine: sa.Engine, adapter: InternalCompilerAdapter, scoped: Any
) -> tuple[Any, list[str]]:
    stmt = adapter._build_select(scoped)
    sql = str(stmt.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
    with engine.connect() as conn:
        json_row = conn.execute(sa.text(f"EXPLAIN FORMAT=JSON {sql}")).fetchone()
        traditional = conn.execute(sa.text(f"EXPLAIN {sql}")).mappings().all()
    if json_row is None or json_row[0] is None:
        raise BenchmarkNotRunnable("EXPLAIN FORMAT=JSON returned no plan")
    raw = json_row[0]
    explain_json = json.loads(raw) if isinstance(raw, str) else raw
    select_types = [
        str(row.get("select_type") or row.get("Select_type") or "") for row in traditional
    ]
    return explain_json, select_types


def _diagnostic_execute(
    engine: sa.Engine,
    principal: Principal,
    bundle: DefinitionBundle,
    scoped: Any,
    identity: str,
) -> dict[str, Any]:
    diagnostic_adapter = InternalCompilerAdapter(
        principal=principal,
        engine=engine,
        bundle=bundle,
        statement_timeout_seconds=_DIAGNOSTIC_TIMEOUT_SECONDS,
        database_identity=identity,
    )
    started = time.perf_counter()
    raw = diagnostic_adapter.execute_with_evidence(scoped)
    duration = round(time.perf_counter() - started, 4)
    return {
        "label": "diagnostic",
        "timeout_seconds": _DIAGNOSTIC_TIMEOUT_SECONDS,
        "duration_seconds": duration,
        "timeout": isinstance(raw, Incomplete) and raw.reason_code == "timeout",
        "raw": raw,
    }


def _shape_payload(
    *,
    shape_id: str,
    digest: str,
    identity: dict[str, Any],
    explain_json: Any,
    select_types: list[str],
    duration: float,
    timed_out: bool,
    raw: Any,
    scoped: Any,
    principal: Principal,
    bundle: DefinitionBundle,
    diagnostic: dict[str, Any] | None,
    declared_causal_difference: str | None = None,
) -> dict[str, Any]:
    answer_query_id = None
    result_identity = None
    member_schema = None
    rows_examined = _rows_examined(explain_json)
    sealed = None
    if isinstance(raw, UnsealedAdapterAnswer):
        sealed = seal_adapter_result(raw, scoped, principal=principal, bundle=bundle)
        answer_query_id = sealed.receipt.answer_query_id
        member_schema = [
            {"name": item.name, "value_kind": item.value_kind, "nullable": item.nullable}
            for item in raw.evidence.result_members
        ]
        result_identity = hashlib.sha256(
            json.dumps(
                list(sealed.rows), sort_keys=True, default=str, separators=(",", ":")
            ).encode()
        ).hexdigest()
    mariadb = {
        "explain_json": explain_json,
        "explain_select_types": select_types,
        "explain_analyze": None,
        "rows_examined": rows_examined,
        "timeout": timed_out,
        "duration_seconds": duration,
        "product_timeout_seconds": _PRODUCT_TIMEOUT_SECONDS,
        "diagnostic_label": diagnostic["label"] if diagnostic else None,
        "diagnostic_timeout_seconds": diagnostic["timeout_seconds"] if diagnostic else None,
        "diagnostic_duration_seconds": diagnostic["duration_seconds"] if diagnostic else None,
        "diagnostic_timeout": diagnostic["timeout"] if diagnostic else None,
    }
    return {
        "shape_id": shape_id,
        "backend": "mariadb",
        "performance_verdict": "NOT_RUN",
        "compiled_query_digest": digest,
        "matched_dataset_identity": identity["matched_dataset_identity"],
        "mariadb_version": identity["mariadb_version"],
        "mariadb_config": identity["mariadb_config"],
        "environment_fingerprint": identity["environment_fingerprint"],
        "index_fingerprint": identity["index_fingerprint"],
        "index_inventory": identity["index_inventory"],
        "answer_query_id": answer_query_id,
        "member_schema": member_schema,
        "result_identity": result_identity,
        "declared_causal_difference": declared_causal_difference,
        "mariadb": mariadb,
    }


def _rows_examined(explain_json: Any) -> int | None:
    rows = 0
    found = False

    def walk(node: Any) -> None:
        nonlocal rows, found
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"rows", "r_rows", "rows_examined_per_scan"} and isinstance(
                    value, (int, float)
                ):
                    found = True
                    rows += int(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(explain_json)
    return rows if found else None
