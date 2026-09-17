"""Evaluation Harness and Canary Runner for Record Detail Adapters.

Benchmarks and verifies the Phase 2 record detail adapters against an independent
oracle with multi-tenant data, checking:
1. Direct record retrieval & field fidelity
2. Permission-based sensitive field masking
3. Child collection reconciliation at parent grain (no Cartesian fan-out)
4. Strict multi-tenant isolation and boundary enforcement
5. Security sanitization (blocking S3 keys, storage paths, signed URLs)

Usage:
    # In-memory self-test / dry-run:
    python scripts/eval/evaluate_record_detail.py --dry-run

    # Live canary database execution:
    python scripts/eval/evaluate_record_detail.py --db-name mcp_local --seed-db
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa  # noqa: E402
from dotenv import dotenv_values  # noqa: E402
from sqlalchemy.engine.url import make_url  # noqa: E402

from app.auth import Principal  # noqa: E402
from app.business_query.authorize.scoping import ScopeDenied  # noqa: E402
from app.business_query.record_detail.customer import CustomerDetailAdapter  # noqa: E402
from app.business_query.record_detail.inventory import (  # noqa: E402
    InventoryDetailAdapter,
    JobArtworkAdapter,
    JobIssuedMaterialAdapter,
)
from app.business_query.record_detail.order import (  # noqa: E402
    CustomerOrderDetailAdapter,
    validate_order_status_transition,
)
from app.business_query.record_detail.planned_materials import (  # noqa: E402
    JobPlannedMaterialAdapter,
)
from app.business_query.record_detail.product_supplier import (  # noqa: E402
    ProductDetailAdapter,
    SupplierDetailAdapter,
)
from app.business_query.record_detail.quotation import QuotationDetailAdapter  # noqa: E402
from tests.fixtures.record_detail.seeder import (  # noqa: E402
    build_in_memory_seeded_engine,
    seed_record_detail_data,
)

logger = logging.getLogger(__name__)


def _db_url_from_env(db_name: str) -> str:
    """Derive target DB URL from .env MCP_BILLING_DATABASE_URL."""
    base = dotenv_values(".env").get("MCP_BILLING_DATABASE_URL")
    if not base:
        raise SystemExit("PREFLIGHT FAIL: MCP_BILLING_DATABASE_URL not found in .env")
    return make_url(base).set(database=db_name).render_as_string(hide_password=False)


def _build_principal(profile: dict[str, Any]) -> Principal:
    return Principal(
        user_id=90001,
        role="eval-runner",
        permissions=list(profile.get("permissions", [])),
        entity_id=profile.get("entity_id"),
        cross_entity=profile.get("cross_entity", False),
    )


def evaluate_case(case: dict[str, Any], engine: sa.engine.Engine) -> dict[str, Any]:
    """Execute a single eval test case and verify against the independent oracle."""
    case_id = case["id"]
    adapter_name = case["adapter"]
    action = case["action"]
    params = case.get("params", {})
    expect = case["expect"]
    oracle = case.get("oracle")

    principal = _build_principal(case["principal"])
    started = time.perf_counter()

    passed = True
    failure_reasons: list[str] = []
    result_data: Any = None
    raised_exception: str | None = None

    try:
        if adapter_name == "CustomerDetailAdapter":
            adapter = CustomerDetailAdapter(engine)
            if action == "get_customer_detail":
                result = adapter.get_customer_detail(
                    customer_id=params["customer_id"],
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                result_data = result.model_dump() if result else None
            elif action == "read_customers":
                results = adapter.read_customers(
                    customer_ids=params["customer_ids"],
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                result_data = [r.model_dump() for r in results]

        elif adapter_name == "CustomerOrderDetailAdapter":
            adapter = CustomerOrderDetailAdapter(engine)
            if action == "get_order_detail":
                result = adapter.get_order_detail(
                    order_id=params["order_id"],
                    entity_id=params["entity_id"],
                )
                if result:
                    result_data = {
                        "id": result.id,
                        "order_number": result.order_number,
                        "status": result.status,
                        "jobs_count": len(result.jobs),
                        "invoices_count": len(result.invoices),
                        "job_numbers": [j.job_number for j in result.jobs],
                        "invoice_numbers": [i.invoice_number for i in result.invoices],
                    }
                else:
                    result_data = None
            elif action == "validate_transitions":
                result_data = {
                    "created_to_active": validate_order_status_transition("CREATED", "ACTIVE"),
                    "created_to_cancelled": validate_order_status_transition(
                        "CREATED", "CANCELLED"
                    ),
                    "active_to_finished": validate_order_status_transition("ACTIVE", "FINISHED"),
                    "finished_to_active": validate_order_status_transition("FINISHED", "ACTIVE"),
                    "cancelled_to_active": validate_order_status_transition("CANCELLED", "ACTIVE"),
                }

        elif adapter_name == "InvoiceDetailAdapter":
            # The eval dataset routes read_open_quotations/read_converted_quotations
            # through this same "InvoiceDetailAdapter" key. QuotationDetailAdapter
            # subclasses InvoiceDetailAdapter, so one instance serves all three actions.
            adapter = QuotationDetailAdapter(engine)
            if action == "read_bill_details":
                results = adapter.read_bill_details(
                    principal=principal,
                    resource_type=params["resource_type"],
                    parent_ids=params["parent_ids"],
                    include_line_items=params.get("include_line_items", False),
                )
                if results:
                    first = results[0]
                    result_data = {
                        "id": first.id,
                        "invoice_number": first.invoice_number,
                        "status": first.status,
                        "items_total": float(first.items_total),
                        "cash_received": float(first.cash_received),
                        "receivable_settlement": float(first.receivable_settlement),
                        "outstanding": float(first.outstanding),
                        "days_past_due": first.days_past_due,
                        "line_items_count": len(first.line_items),
                    }
                else:
                    result_data = []
            elif action == "read_open_quotations":
                results = adapter.read_open_quotations(
                    principal=principal, parent_ids=params["parent_ids"]
                )
                result_data = {"open_ids": [r.id for r in results]}
            elif action == "read_converted_quotations":
                results = adapter.read_converted_quotations(
                    principal=principal, parent_ids=params["parent_ids"]
                )
                result_data = {"converted_ids": [r.id for r in results]}

        elif adapter_name == "JobPlannedMaterialAdapter":
            adapter = JobPlannedMaterialAdapter(engine)
            if action == "get_planned_materials":
                results = adapter.get_planned_materials(
                    job_id=params.get("job_id"),
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                result_data = {
                    "item_count": len(results),
                    "products": [r.product_name for r in results],
                    "quantities": [float(r.quantity) for r in results],
                }

        elif adapter_name == "JobIssuedMaterialAdapter":
            adapter = JobIssuedMaterialAdapter(engine)
            if action == "get_issued_materials":
                results = adapter.get_issued_materials(
                    job_id=params.get("job_id"),
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                result_data = {
                    "item_count": len(results),
                    "products": [r.product_name for r in results],
                    "quantities": [float(r.quantity) for r in results],
                    "batch_number": results[0].batch_number if results else None,
                }

        elif adapter_name == "InventoryDetailAdapter":
            adapter = InventoryDetailAdapter(engine)
            if action == "get_inventory":
                result = adapter.get_inventory(
                    inventory_id=params["inventory_id"],
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                if result:
                    result_data = {
                        "id": result.id,
                        "product_name": result.product_name,
                        "quantity_on_hand": float(result.quantity_on_hand),
                        "unit_cost": float(result.unit_cost)
                        if result.unit_cost is not None
                        else None,
                        "total_valuation": (
                            float(result.total_valuation)
                            if result.total_valuation is not None
                            else None
                        ),
                    }
                else:
                    result_data = None

        elif adapter_name == "JobArtworkAdapter":
            adapter = JobArtworkAdapter(engine)
            if action == "get_artwork_metadata":
                results = adapter.get_artwork_metadata(
                    job_id=params["job_id"],
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                if results:
                    first = results[0]
                    dumped = first.model_dump()
                    result_data = {
                        "file_name": first.file_name,
                        "status": first.status,
                        "storage_path_absent": "storage_path" not in dumped,
                        "s3_key_absent": "s3_key" not in dumped,
                        "signed_url_absent": "signed_url" not in dumped,
                    }
                else:
                    result_data = None

        elif adapter_name == "ProductDetailAdapter":
            adapter = ProductDetailAdapter(engine)
            if action == "get_product":
                result = adapter.get_product(
                    product_id=params["product_id"],
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                if result:
                    result_data = {
                        "id": result.id,
                        "name": result.name,
                        "sku": result.sku,
                        "category": result.category,
                        "unit_price": float(result.unit_price)
                        if result.unit_price is not None
                        else None,
                    }
                else:
                    result_data = None

        elif adapter_name == "SupplierDetailAdapter":
            adapter = SupplierDetailAdapter(engine)
            if action == "get_supplier":
                result = adapter.get_supplier(
                    supplier_id=params["supplier_id"],
                    entity_id=params["entity_id"],
                    principal=principal,
                )
                if result:
                    result_data = {
                        "id": result.id,
                        "name": result.name,
                        "code": result.code,
                        "payment_terms": result.payment_terms,
                    }
                else:
                    result_data = None

    except ScopeDenied:
        raised_exception = "ScopeDenied"
    except Exception as exc:
        raised_exception = type(exc).__name__
        passed = False
        failure_reasons.append(f"Unexpected exception: {type(exc).__name__}: {exc}")

    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    # Oracle verification
    if expect == "empty":
        if result_data is not None and result_data != []:
            passed = False
            failure_reasons.append(f"Expected empty/None result, got {result_data}")
    elif expect == "denied":
        if raised_exception != "ScopeDenied":
            passed = False
            failure_reasons.append(f"Expected ScopeDenied exception, got {raised_exception}")
    elif expect == "resolved":
        if raised_exception is not None:
            passed = False
            failure_reasons.append(f"Expected resolution, but exception raised: {raised_exception}")
        elif oracle is not None and isinstance(oracle, dict):
            for k, expected_v in oracle.items():
                if k == "excluded_ids":
                    continue
                actual_v = result_data.get(k) if isinstance(result_data, dict) else None
                if actual_v != expected_v:
                    passed = False
                    failure_reasons.append(
                        f"Field '{k}' mismatch: expected {expected_v!r}, got {actual_v!r}"
                    )

    return {
        "id": case_id,
        "stratum": case.get("stratum"),
        "question": case.get("question"),
        "adapter": adapter_name,
        "action": action,
        "expect": expect,
        "passed": passed,
        "failure_reasons": failure_reasons,
        "result": result_data,
        "exception": raised_exception,
        "latency_ms": latency_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Detail Adapter Evaluation Harness")
    parser.add_argument(
        "--questions",
        default="evals/records/record_detail_cases.jsonl",
        help="Path to questions JSONL dataset",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--db-url", help="Database URL")
    group.add_argument("--db-name", help="Database name in .env MCP_BILLING_DATABASE_URL")
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Use in-memory seeded SQLite engine (zero external dependencies)",
    )
    parser.add_argument(
        "--seed-db", action="store_true", help="Seed test records into target database before run"
    )
    parser.add_argument("--out", help="Output directory for results")
    parser.add_argument(
        "--strict", action="store_true", help="Exit non-zero if any test case fails"
    )
    args = parser.parse_args()

    # Determine database engine
    if args.dry_run or (not args.db_url and not args.db_name):
        print("Initializing in-memory seeded SQLite engine for record detail evaluation...")
        engine = build_in_memory_seeded_engine()
    else:
        db_url = args.db_url or _db_url_from_env(args.db_name)
        engine = sa.create_engine(db_url, pool_pre_ping=True)
        if args.seed_db:
            print(f"Seeding record detail test data into database: {engine.url.database}...")
            seed_record_detail_data(engine)

    # Load question dataset
    questions_file = Path(args.questions)
    if not questions_file.exists():
        print(f"ERROR: Question file not found: {questions_file}")
        return 1

    cases = [
        json.loads(line)
        for line in questions_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"Loaded {len(cases)} evaluation cases from {questions_file}")

    results: list[dict[str, Any]] = []
    for c in cases:
        res = evaluate_case(c, engine)
        status = "PASSED" if res["passed"] else "FAILED"
        print(f"[{status}] {res['id']} ({res['stratum']}) - {res['latency_ms']}ms")
        if not res["passed"]:
            for r in res["failure_reasons"]:
                print(f"    - {r}")
        results.append(res)

    # Summary statistics
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    failed_count = total - passed_count
    stratum_stats: dict[str, dict[str, int]] = {}

    for r in results:
        strat = r["stratum"] or "default"
        if strat not in stratum_stats:
            stratum_stats[strat] = {"total": 0, "passed": 0, "failed": 0}
        stratum_stats[strat]["total"] += 1
        if r["passed"]:
            stratum_stats[strat]["passed"] += 1
        else:
            stratum_stats[strat]["failed"] += 1

    summary = {
        "timestamp": datetime.now(UTC).isoformat(),
        "total_cases": total,
        "passed": passed_count,
        "failed": failed_count,
        "pass_rate_pct": round((passed_count / total) * 100, 2) if total else 0.0,
        "stratum_breakdown": stratum_stats,
    }

    # Write output artifacts
    timestamp_slug = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else Path(f"data/eval-runs/record-detail-{timestamp_slug}")
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, default=str, ensure_ascii=False) for r in results) + "\n",
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, default=str, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print("\n" + "=" * 50)
    print(f"SUMMARY: {passed_count}/{total} cases passed ({summary['pass_rate_pct']}%)")
    print(f"Artifacts written to: {out_dir}")
    print("=" * 50)

    if args.strict and failed_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
