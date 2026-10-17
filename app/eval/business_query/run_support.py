"""Paid-suite helpers for the Business Query eval CLI."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.business_query.case_repeats import CaseRepeatLifecycle, PostgresCaseRepeatSink
from app.business_query.composition import ModulePlugins, build_module
from app.business_query.outcomes import Answered, resolver_record_fields
from app.business_query.seal.events import (
    EventAccessContext,
    EventEncryptionKeyring,
    EventPayloadMode,
    ExecutionEventStore,
    PostgresExecutionEventStore,
)
from app.business_query.wire.module import (
    BusinessQueryEvidenceContext,
    BusinessQueryEvidencePorts,
    BusinessQueryRequest,
)
from app.business_query.wire.trace import QueryTrace
from app.core.turn_budget import UNBOUNDED_BUDGET
from app.eval.business_query.harness import BUSINESS_DATE, SpendCeiling, eval_principal
from app.eval.business_query.preflight import EVAL_ADAPTER_STATEMENT_TIMEOUT_SECONDS
from app.eval.business_query.readiness import EvaluationReadinessError
from app.eval.business_query.receipt import receipt_valid
from app.eval.business_query.score_resolved import score_resolved_execution
from app.eval.business_query.scorer import CaseScore, CaseScoringSpec

_ROWS_KEPT = 25


async def finish_error_preserving(
    lifecycle: CaseRepeatLifecycle,
    terminal_class: str,
    terminal_code: str,
    original: BaseException,
) -> None:
    try:
        await lifecycle.finish_error(terminal_class, terminal_code)
    except Exception as finish_exc:
        raise finish_exc from original


async def execute_scored_repeat(
    lifecycle: CaseRepeatLifecycle,
    execute: Callable[[], Awaitable[dict]],
    score: Callable[[dict], Awaitable[CaseScore]],
) -> tuple[dict, CaseScore]:
    """Run one repeat with one durable terminal, then preserve the original failure."""
    await lifecycle.start()
    try:
        run = await execute()
    except asyncio.CancelledError as exc:
        await finish_error_preserving(lifecycle, "cancelled", "CancelledError", exc)
        raise
    except SpendCeiling as exc:
        await finish_error_preserving(lifecycle, "budget_stop", "SpendCeiling", exc)
        raise
    except Exception as exc:
        await finish_error_preserving(lifecycle, "evaluator_error", type(exc).__name__, exc)
        raise

    try:
        scored = await score(run)
    except asyncio.CancelledError as exc:
        await finish_error_preserving(lifecycle, "cancelled", "CancelledError", exc)
        raise
    except Exception as exc:
        await finish_error_preserving(lifecycle, "scorer_error", type(exc).__name__, exc)
        raise

    await lifecycle.finish_outcome(
        outcome=run["outcome"],
        reason_code=run.get("reason_code", ""),
        answer_query_id=run.get("answer_query_id"),
        planner_attempt_id=run.get("planner_attempt_id"),
    )
    return run, scored


class Diagnostics(logging.Handler):
    """Capture module warnings for one case without LangSmith rate-limit noise."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("langsmith"):
            return
        self.lines.append(f"{record.name}: {record.getMessage()}")


def write_results(out_dir: Path, results: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in results) + "\n",
        encoding="utf-8",
    )


def event_keyring() -> EventEncryptionKeyring:
    from app.config import settings

    if settings.business_query_event_payload_mode != "encrypted":
        raise EvaluationReadinessError("evaluation event payload mode must be encrypted")
    if not settings.business_query_event_encryption_keys:
        raise EvaluationReadinessError("evaluation event encryption keyring is unavailable")
    try:
        return EventEncryptionKeyring.parse(settings.business_query_event_encryption_keys)
    except ValueError as exc:
        raise EvaluationReadinessError("evaluation event encryption keyring is invalid") from exc


def database_identity(preflight: dict) -> str:
    database = preflight["database"]
    return (
        f"{database['host']}:{database['port']}/{database['name']}:{database['views_fingerprint']}"
    )


def evidence_context(
    *,
    run_id: str,
    case_id: str,
    repeat_index: int,
    preflight: dict,
) -> BusinessQueryEvidenceContext:
    from app.config import settings

    arm = preflight["arm"]
    now = datetime.now(tz=UTC)
    return BusinessQueryEvidenceContext(
        idempotency_key=f"{run_id}:{case_id}:{repeat_index}:answer",
        project_id=settings.query_record_project_id,
        retention_at=now + timedelta(days=settings.business_query_event_retention_days),
        payload_mode=EventPayloadMode.ENCRYPTED,
        payload_classification="evaluation",
        route=arm["route_id"],
        provider=arm["provider"],
        deployment=arm["deployment"],
        output_mode=arm["structured_output_mode"],
        effort=arm["reasoning_effort"],
        case_id=case_id,
        repeat_index=repeat_index,
    )


def build_eval_module(
    *,
    principal,
    engine,
    bundle,
    recorder,
    trace,
    executor: ThreadPoolExecutor | None = None,
    event_store: ExecutionEventStore | None = None,
    database_identity: str | None = None,
):
    """Eval builder — route-derived module budget, explicit 120s DB statement kill."""
    from app.config import settings

    if executor is None:
        executor = ThreadPoolExecutor(max_workers=settings.adapter_executor_max_workers)

    return build_module(
        principal=principal,
        engine=engine,
        executor=executor,
        bundle=bundle,
        database_identity=database_identity,
        statement_timeout_seconds=EVAL_ADAPTER_STATEMENT_TIMEOUT_SECONDS,
        plugins=ModulePlugins(
            trace=trace,
            call_recorder=recorder,
            evidence_ports=(
                BusinessQueryEvidencePorts(event_store=event_store)
                if event_store is not None
                else None
            ),
            pagination_secret=settings.rag_jwt_secret,
        ),
    )


async def run_one(
    case: dict,
    engine,
    bundle,
    recorder,
    *,
    event_store: ExecutionEventStore | None = None,
    evidence: BusinessQueryEvidenceContext | None = None,
    database_identity: str | None = None,
) -> dict:
    principal = eval_principal(case)
    trace = QueryTrace()
    trace.capture_planner_payload = True
    module = build_eval_module(
        principal=principal,
        engine=engine,
        bundle=bundle,
        recorder=recorder,
        trace=trace,
        event_store=event_store,
        database_identity=database_identity,
    )
    request = BusinessQueryRequest(
        question=case["question"],
        principal=principal,
        correlation_id=f"bqeval-{case['id']}",
        business_date=BUSINESS_DATE,
    )
    diagnostics = Diagnostics()
    root = logging.getLogger()
    root.addHandler(diagnostics)
    started = time.perf_counter()
    try:
        outcome = await module.query(request, evidence=evidence, turn_budget=UNBOUNDED_BUDGET)
    finally:
        root.removeHandler(diagnostics)
    receipt = outcome.receipt.model_dump(mode="json") if isinstance(outcome, Answered) else None
    rows = list(outcome.rows) if isinstance(outcome, Answered) else None
    total_row_count = outcome.total_row_count if isinstance(outcome, Answered) else None
    returned_row_count = len(rows) if rows is not None else None
    result_member_schema = tuple(rows[0]) if rows else () if rows is not None else None
    truncated = (
        total_row_count > returned_row_count
        if total_row_count is not None and returned_row_count is not None
        else None
    )
    resolver_query_id, _disposition = resolver_record_fields(outcome)
    return {
        "outcome": outcome.outcome,
        "rows": rows,
        "result_member_schema": result_member_schema,
        "returned_row_count": returned_row_count,
        "total_row_count": total_row_count,
        "truncated": truncated,
        "receipt": receipt,
        "answer_query_id": receipt["answer_query_id"] if receipt else None,
        "resolver_query_id": resolver_query_id,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "reason_code": getattr(outcome, "reason_code", None) or "",
        "message": getattr(outcome, "message", None) or getattr(outcome, "question", "") or "",
        "diagnostics": diagnostics.lines,
        "trace": trace.as_dict(),
    }


def attempt_record(case: dict, run: dict, score: CaseScore, *, bundle_hash: str) -> dict:
    receipt_ok = receipt_valid(
        case,
        run,
        bundle_hash=bundle_hash,
        manifest_hash=eval_principal(case).manifest_hash,
    )
    passed = score.passed and receipt_ok
    detail = score.detail
    if score.passed and not receipt_ok:
        detail = "answered outcome missing a valid executor receipt"
    return {
        "case_id": case["id"],
        "family": case["family"],
        "phrasing": case["phrasing"],
        "expected": score.outcome_expected,
        "actual": score.outcome_actual,
        "outcome_match": score.outcome_match,
        "literal_match": score.literal_match,
        "passed": passed,
        "detail": detail,
        "receipt_valid": receipt_ok,
        "receipt": run["receipt"],
        "answer_query_id": run["answer_query_id"],
        "reason_code": run["reason_code"],
        "message": run["message"],
        "diagnostics": run["diagnostics"],
        **run["trace"],
        "rows": run["rows"][:_ROWS_KEPT] if run["rows"] is not None else None,
        "row_count": len(run["rows"]) if run["rows"] is not None else None,
        "result_member_schema": run["result_member_schema"],
        "returned_row_count": run["returned_row_count"],
        "total_row_count": run["total_row_count"],
        "truncated": run["truncated"],
        "latency_ms": run["latency_ms"],
    }


def print_case_attempts(case_id: str, attempts: list[dict], *, quiet: bool) -> None:
    if quiet or not attempts:
        return
    wins = sum(1 for attempt in attempts if attempt["passed"])
    stability = "PASS" if wins == len(attempts) else ("FAIL" if wins == 0 else "FLAKY")
    outcomes = {attempt["actual"] for attempt in attempts}
    last = attempts[-1]
    print(
        f"  {case_id} {stability:5} {wins}/{len(attempts)} "
        f"{'|'.join(sorted(outcomes)):<34} {last['detail']}"
    )
    timing = f"planner {last['planner_ms'] or 0:.0f}ms"
    if last["sql_ms"] is not None:
        timing += f" · sql {last['sql_ms']:.0f}ms · {last['rows_returned']} rows"
    print(f"        {timing}")
    if last["failure_layer"]:
        print(f"        failed in {last['failure_layer']}: {(last['failure_detail'] or '')[:200]}")
    if last["missing_members"]:
        print(f"        missing members: {', '.join(last['missing_members'])}")


async def run_paid_suite(
    *,
    cases: dict[str, dict],
    oracle: dict[str, dict],
    scoring_specs: dict[str, CaseScoringSpec],
    engine,
    bundle,
    recorder,
    preflight: dict,
    run_id: str,
    event_keyring: EventEncryptionKeyring,
    session_factory,
    repeats: int,
    out_dir: Path,
    quiet: bool,
) -> list[dict]:
    from app.config import settings
    from app.telemetry.invocation_ledger import register_event_loop

    register_event_loop()

    results: list[dict] = []
    db_identity = database_identity(preflight)
    project_id = settings.query_record_project_id
    async with session_factory() as event_session:
        event_store = PostgresExecutionEventStore(event_session, keyring=event_keyring)
        case_sink = PostgresCaseRepeatSink(event_session)
        for case_id in sorted(cases):
            case = cases[case_id]
            attempts: list[dict] = []
            aborted = False
            for repeat_index in range(repeats):
                evidence = evidence_context(
                    run_id=run_id,
                    case_id=case_id,
                    repeat_index=repeat_index,
                    preflight=preflight,
                )
                lifecycle = CaseRepeatLifecycle(
                    case_sink,
                    project_id=project_id,
                    run_epoch=run_id,
                    case_id=case_id,
                    repeat_index=repeat_index,
                )

                async def execute(
                    case=case,
                    evidence=evidence,
                ) -> dict:
                    return await run_one(
                        case,
                        engine,
                        bundle,
                        recorder,
                        event_store=event_store,
                        evidence=evidence,
                        database_identity=db_identity,
                    )

                async def score(
                    run: dict,
                    case=case,
                    case_id=case_id,
                ) -> CaseScore:
                    return await score_resolved_execution(
                        case,
                        oracle.get(case_id),
                        scoring_specs.get(case_id),
                        run,
                        resolver=event_store,
                        access=EventAccessContext(project_id, "evaluation"),
                    )

                try:
                    run, score_result = await execute_scored_repeat(lifecycle, execute, score)
                except SpendCeiling as exc:
                    print(f"ABORTED: {exc}")
                    aborted = True
                    break
                except EvaluationReadinessError as exc:
                    print(f"ABORTED: structural evaluation invalidity: {exc}")
                    aborted = True
                    break
                except Exception as exc:
                    print(f"ABORTED: {type(exc).__name__}: {exc}")
                    aborted = True
                    break
                record = attempt_record(case, run, score_result, bundle_hash=bundle.content_hash)
                attempts.append(record)
                results.append(record)
                write_results(out_dir, results)
            print_case_attempts(case_id, attempts, quiet=quiet)
            if aborted:
                break
    return results
