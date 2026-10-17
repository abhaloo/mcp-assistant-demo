"""Q1 old-consumer execute and bill-fact view rollback on mcp_bq_eval."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

import sqlalchemy as sa

from app.auth import Principal
from app.business_query.authorize.scoping import apply_role_scope
from app.business_query.compile.adapter import InternalCompilerAdapter
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import Incomplete
from app.business_query.plan import BusinessQueryPlan
from app.business_query.seal.evidence import seal_adapter_result
from app.business_query.wire.module import UnsealedAdapterAnswer
from app.experiments.bq_query_performance.benchmark import (
    BenchmarkNotRunnable,
    BenchmarkPreflightError,
    _assert_frozen_execute_identity,
    _shape,
)
from app.experiments.bq_query_performance.mariadb_baseline import read_matched_dataset_identity

_EVAL_DATABASE = "mcp_bq_eval"
_BILL_FACT_VIEW = "ai_v1_bq_bill_fact"
_PRODUCT_TIMEOUT_SECONDS = 10.0
_MARIADB_ENTITY_ID = 1
_FROZEN_BILLS_ENTITY1 = 4830
CORRELATED_VIEW_SHA256 = "7150e1dc1ddc2210de70c8c490f5afeaadab158218009ccceca1779f3cdb924d"
SET_BASED_VIEW_SHA256 = "6eb8dc649721c226a376c0d6e5df5bbd5aa21424e4066de9963cee5d753cafda"
BILL_FACT_COLUMNS = [
    "id",
    "invoice_number",
    "customer_id",
    "customer_name",
    "type",
    "payable",
    "status",
    "created_at",
    "due_date",
    "currency_code",
    "items_total",
    "cash_received",
    "receivable_settlement",
    "outstanding",
    "days_past_due",
    "entity_id",
]
Q1_SHAPE_IDS = ("overdue_outstanding", "ar_aging", "invoices_count")
_CREATE_VIEW_RE = re.compile(
    r"^CREATE\s+OR\s+REPLACE\s+VIEW\s+ai_v1_bq_bill_fact\s+AS\s+",
    re.IGNORECASE,
)


def assert_eval_database(database: str | None) -> None:
    if database != _EVAL_DATABASE:
        raise BenchmarkPreflightError(
            f"Q1 view rollback is allowed only on {_EVAL_DATABASE}, got {database!r}"
        )


def judge_q1_consumer_compatibility(
    contract: Mapping[str, Any], captures: Mapping[str, Any]
) -> None:
    """Pin old-consumer execute of the three Q1 shapes to the freeze."""
    frozen = contract.get("mariadb_result_identity") or {}
    digests = contract.get("shape_compiled_query_digests") or {}
    for shape_id in Q1_SHAPE_IDS:
        payload = captures.get(shape_id)
        expected = frozen.get(shape_id) or {}
        if not isinstance(payload, Mapping):
            raise BenchmarkNotRunnable(f"Q1 consumer capture missing {shape_id}")
        _require(
            f"q1.{shape_id}.member_schema",
            expected.get("member_schema"),
            payload.get("member_schema"),
        )
        _require(
            f"q1.{shape_id}.row_sha256",
            expected.get("row_sha256"),
            payload.get("result_identity"),
        )
        _require(
            f"q1.{shape_id}.compiled_query_digest",
            digests.get(shape_id),
            payload.get("compiled_query_digest"),
        )
        _require(
            f"q1.{shape_id}.product_timeout",
            False,
            payload.get("product_timeout"),
        )


def judge_q1_view_rollback(receipt: Mapping[str, Any]) -> None:
    """Pin counts, COLUMNS, and view-hash round-trip on mcp_bq_eval."""
    before = receipt.get("before") or {}
    mid = receipt.get("mid") or {}
    after = receipt.get("after") or {}
    _require("rollback.counts", before.get("counts"), mid.get("counts"))
    _require("rollback.counts", before.get("counts"), after.get("counts"))
    _require("rollback.columns", list(BILL_FACT_COLUMNS), before.get("columns"))
    _require("rollback.columns", list(BILL_FACT_COLUMNS), mid.get("columns"))
    _require("rollback.columns", list(BILL_FACT_COLUMNS), after.get("columns"))
    _require("rollback.before.view_sha256", SET_BASED_VIEW_SHA256, before.get("view_sha256"))
    _require("rollback.mid.view_sha256", CORRELATED_VIEW_SHA256, mid.get("view_sha256"))
    _require("rollback.after.view_sha256", SET_BASED_VIEW_SHA256, after.get("view_sha256"))
    bills_entity1 = (before.get("counts") or {}).get("bills_entity1")
    _require("rollback.bills_entity1", _FROZEN_BILLS_ENTITY1, bills_entity1)


def snapshot_bill_fact_state(engine: sa.Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        database = conn.execute(sa.text("SELECT DATABASE()")).scalar()
        assert_eval_database(str(database) if database is not None else None)
        bills = conn.execute(sa.text("SELECT COUNT(*) FROM bills")).scalar()
        journals = conn.execute(sa.text("SELECT COUNT(*) FROM journals")).scalar()
        bills_entity1 = conn.execute(
            sa.text("SELECT COUNT(*) FROM bills WHERE entity_id = :entity_id"),
            {"entity_id": _MARIADB_ENTITY_ID},
        ).scalar()
        columns = [
            str(row[0])
            for row in conn.execute(
                sa.text(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :view_name "
                    "ORDER BY ORDINAL_POSITION"
                ),
                {"view_name": _BILL_FACT_VIEW},
            )
        ]
        create_row = conn.execute(sa.text(f"SHOW CREATE VIEW {_BILL_FACT_VIEW}")).fetchone()
    if create_row is None:
        raise BenchmarkNotRunnable("bill-fact view could not be read")
    view_sql = str(create_row[1])
    return {
        "view_sha256": hashlib.sha256(view_sql.encode("utf-8")).hexdigest(),
        "columns": columns,
        "counts": {
            "bills": int(bills or 0),
            "journals": int(journals or 0),
            "bills_entity1": int(bills_entity1 or 0),
        },
    }


def replace_bill_fact_view(engine: sa.Engine, apply_sql: str) -> None:
    statement = apply_sql.strip().rstrip(";")
    if not _CREATE_VIEW_RE.match(statement) or ";" in statement:
        raise BenchmarkPreflightError("bill-fact rollback SQL is not a single CREATE OR REPLACE")
    with engine.begin() as conn:
        database = conn.execute(sa.text("SELECT DATABASE()")).scalar()
        assert_eval_database(str(database) if database is not None else None)
        conn.exec_driver_sql(statement)


def prove_q1_view_rollback_round_trip(
    engine: sa.Engine,
    *,
    correlated_sql: str,
    set_based_sql: str,
) -> dict[str, Any]:
    """Restore correlated bill-fact SQL, then the set-based view, without row loss."""
    before = snapshot_bill_fact_state(engine)
    try:
        replace_bill_fact_view(engine, correlated_sql)
        mid = snapshot_bill_fact_state(engine)
        replace_bill_fact_view(engine, set_based_sql)
        after = snapshot_bill_fact_state(engine)
        receipt = {"before": before, "mid": mid, "after": after}
        judge_q1_view_rollback(receipt)
        return receipt
    except Exception:
        replace_bill_fact_view(engine, set_based_sql)
        raise


def execute_q1_consumer_shapes(
    adapter: InternalCompilerAdapter,
    principal: Principal,
    bundle: DefinitionBundle,
    contract: Mapping[str, Any],
    *,
    business_date: date | None = None,
) -> dict[str, dict[str, Any]]:
    """Execute the three frozen Q1 plans through the current RAG consumer."""
    if adapter._statement_timeout_seconds != _PRODUCT_TIMEOUT_SECONDS:
        raise BenchmarkPreflightError("Q1 consumer execute must use the product 10s statement cap")
    engine = adapter._engine
    identity = read_matched_dataset_identity(engine)
    adapter._database_identity = identity["matched_dataset_identity"]
    execute_contract = json.loads(json.dumps(dict(contract)))
    execute_contract["principal"] = dict(execute_contract.get("principal") or {})
    execute_contract["principal"]["entity_id"] = principal.entity_id
    frozen_date = _assert_frozen_execute_identity(
        principal, bundle, execute_contract, business_date
    )
    captures: dict[str, dict[str, Any]] = {}
    for shape_id in Q1_SHAPE_IDS:
        shape = _shape(execute_contract, shape_id)
        plan = BusinessQueryPlan.model_validate(shape["plan"])
        scoped = apply_role_scope(plan, principal, bundle, business_date=frozen_date)
        digest = hashlib.sha256(adapter.compile_sql(scoped).encode()).hexdigest()
        raw = adapter.execute_with_evidence(scoped)
        timed_out = isinstance(raw, Incomplete) and raw.reason_code == "timeout"
        member_schema = None
        result_identity = None
        if isinstance(raw, UnsealedAdapterAnswer):
            sealed = seal_adapter_result(raw, scoped, principal=principal, bundle=bundle)
            member_schema = [
                {"name": item.name, "value_kind": item.value_kind, "nullable": item.nullable}
                for item in raw.evidence.result_members
            ]
            result_identity = hashlib.sha256(
                json.dumps(
                    list(sealed.rows), sort_keys=True, default=str, separators=(",", ":")
                ).encode()
            ).hexdigest()
        captures[shape_id] = {
            "member_schema": member_schema,
            "result_identity": result_identity,
            "compiled_query_digest": digest,
            "product_timeout": timed_out,
        }
    return captures


def _require(label: str, expected: Any, observed: Any) -> None:
    if expected != observed:
        raise BenchmarkNotRunnable(f"Q1 compatibility drifted: {label}")
