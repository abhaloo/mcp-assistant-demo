"""CLI evaluation runner for the conversational coordinator.

Delegates all scoring to app.eval.conversation_coordinator.score per ADR 0066.
Supports --cases <jsonl>, --out <dir>, and --baseline.

Every request is an Ask protocol version 2 body with a fresh one-time token.
The candidate route is scored only after the server reports the coordinator
flag it is labelled with; otherwise the capture is unproven.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import EffectiveRouteSettings
from app.conversation.coordinator.contracts import CoordinatorContext
from app.eval.conversation_coordinator import (
    CaseResult,
    CoordinatorCase,
    CoordinatorRunOutput,
    load_coordinator_cases,
    score,
)
from app.telemetry.invocation_ledger import ledger_store_configured
from app.telemetry.invocation_payload import get_recorded_evidence
from scripts.eval.coordinator_eval_stats import (
    TraceContext,
    build_trace_row,
    compare_baseline,
    count_violations,
    median,
    percentile,
)
from scripts.eval.deadline_probe_arms import ARM_PRESETS
from scripts.eval.deadline_probe_spend import (
    SOFT_ALERT_FRAC,
    SpendEnvelope,
    emit_heartbeat,
    live_spend_required,
    open_spend_ledger,
    refuse_live_spend_start,
)
from scripts.eval.deadline_probe_telemetry import (
    append_answer_jsonl,
    apply_durable_usage,
    empty_case_ledger_slice,
    merge_case_ledger_into_row,
)
from scripts.eval.eval_tokens import DEFAULT_PROFILE, PROFILES, mint_eval_token

DEFAULT_CASES_PATH = "evals/conversation_coordinator/cases.jsonl"
DEFAULT_OUT_DIR = "data/conversation-coordinator-eval"
LIVE_ENV_FLAG = "RUN_CONVERSATION_COORDINATOR_LIVE"
BASELINE_REPEATS = 3
TURN_DEADLINE_MS = 25_000
CANDIDATE_ROUTE = "ask_v2_coordinator_on"
BASELINE_ROUTE = "ask_v2_coordinator_off"
SIMPLE_BQ_SUITE_PATH = Path("evals/prod_ask_e2e/suite.json")
ExecuteRun = Callable[[CoordinatorCase, bool], CoordinatorRunOutput]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run evaluation or timing baseline for conversational coordinator."
    )
    parser.add_argument(
        "--cases",
        default=DEFAULT_CASES_PATH,
        help="Path to cases JSONL file.",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT_DIR,
        help="Directory to write results and summary.",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Run baseline timing capture with coordinator capability disabled.",
    )
    parser.add_argument(
        "--only",
        default="",
        help="Comma-separated case IDs to filter execution.",
    )
    parser.add_argument(
        "--baseline-file",
        default="",
        help="Frozen baseline JSON to compare candidate hashes and route ids.",
    )
    parser.add_argument(
        "--arm",
        choices=tuple(ARM_PRESETS),
        default="current",
        help="Deadline probe arm preset (current, plus50, unbounded).",
    )
    parser.add_argument(
        "--deadline-ms",
        type=int,
        default=None,
        help="Override client deadline_at_ms offset for this run.",
    )
    parser.add_argument(
        "--http-timeout-s",
        type=float,
        default=None,
        help="Override HTTP wait timeout for Ask POSTs.",
    )
    parser.add_argument(
        "--campaign-cap-usd",
        type=str,
        default=None,
        help="Campaign hard cap in USD (required on live eval).",
    )
    parser.add_argument(
        "--spend-ledger",
        default="",
        help="Shared spend ledger JSON path (required on live eval).",
    )
    parser.add_argument(
        "--cell-ceiling-usd",
        type=str,
        default=None,
        help="Per-case cell ceiling in USD after first priced receipt.",
    )
    parser.add_argument(
        "--soft-alert-frac",
        type=float,
        default=SOFT_ALERT_FRAC,
        help="Soft alert fraction of campaign cap.",
    )
    parser.add_argument(
        "--arm-wall-s",
        type=int,
        default=7200,
        help="Wall-clock abort threshold per arm in seconds.",
    )
    return parser.parse_args(argv)


def _eval_base_url(is_baseline: bool = False) -> str:
    if is_baseline:
        return (os.environ.get("COORDINATOR_EVAL_BASELINE_URL") or "").rstrip("/")
    return (
        os.environ.get("COORDINATOR_EVAL_BASE_URL") or os.environ.get("RAG_BASE_URL") or ""
    ).rstrip("/")


def _readyz(base: str) -> tuple[bool, str, dict[str, Any]]:
    """Readiness plus the body, so the caller can confirm the route flags."""
    url = f"{base}/readyz"
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=2) as resp:
            status = getattr(resp, "status", 0)
            raw = resp.read()
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return False, f"readyz failed: {exc}", {}
    if status != 200:
        return False, f"readyz {status}", {}
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        body = {}
    return True, "readyz 200", body if isinstance(body, dict) else {}


def _route_confirmed(base: str, *, coordinator_on: bool, arm: str = "current") -> tuple[bool, str]:
    """The route label is a claim about the server; the server must agree."""
    ready, reason, body = _readyz(base)
    if not ready:
        return False, reason
    key = EffectiveRouteSettings.BODY_KEY
    try:
        effective = EffectiveRouteSettings.model_validate(body.get(key))
    except ValidationError:
        return False, f"readyz has no valid {key}; the route cannot be confirmed"
    flag = effective.conversation_coordinator_enabled
    if flag is not coordinator_on:
        return False, (
            f"coordinator flag mismatch: server reports conversation_coordinator_enabled="
            f"{flag!r}, route expects {coordinator_on}"
        )
    preset = ARM_PRESETS.get(arm, ARM_PRESETS["current"])
    clock_mismatches: list[str] = []
    if effective.ask_turn_unbounded != preset["unbounded"]:
        clock_mismatches.append(
            f"ask_turn_unbounded server={effective.ask_turn_unbounded!r} "
            f"expected={preset['unbounded']!r}"
        )
    if effective.ask_max_deadline_ms != preset["max_deadline_ms"]:
        clock_mismatches.append(
            f"ask_max_deadline_ms server={effective.ask_max_deadline_ms!r} "
            f"expected={preset['max_deadline_ms']!r}"
        )
    if effective.ask_planner_step_ceiling_seconds != preset["planner"]:
        clock_mismatches.append(
            f"ask_planner_step_ceiling_seconds server="
            f"{effective.ask_planner_step_ceiling_seconds!r} expected={preset['planner']!r}"
        )
    if clock_mismatches:
        return False, f"clock mismatch: {'; '.join(clock_mismatches)}"
    return True, f"{reason}; coordinator flag confirmed"


def exclusive_live_env_ready(
    *, is_baseline: bool = False, arm: str = "current"
) -> tuple[bool, str]:
    """Live timings require the live flag, the route's base URL, /readyz 200,
    and the server confirming the coordinator flag the route is labelled with."""
    if os.environ.get(LIVE_ENV_FLAG) != "1":
        return False, "RUN_CONVERSATION_COORDINATOR_LIVE is not 1; exclusive env not claimed"
    base = _eval_base_url(is_baseline)
    if not base:
        variable = "COORDINATOR_EVAL_BASELINE_URL" if is_baseline else "COORDINATOR_EVAL_BASE_URL"
        return False, f"{variable} unset; exclusive env not claimed"
    return _route_confirmed(base, coordinator_on=not is_baseline, arm=arm)


def _post_ask(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw) if raw else {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _unproven(
    route_id: str,
    *,
    reason: str,
    run_ids: tuple[str, ...] = (),
    thread_id: str | None = None,
) -> CoordinatorRunOutput:
    """An observation the run could not complete, with why and which request
    ids to look for in the server capture."""
    return CoordinatorRunOutput(
        capture_complete=False,
        status="unproven",
        route_id=route_id,
        reason=reason,
        run_ids=run_ids,
        thread_id=thread_id,
    )


def _bearer_for(case: CoordinatorCase) -> str:
    """A fresh v2 eval token for the case's principal on every request: the
    verifier consumes each jti once, so no shared bearer can serve a run."""
    return mint_eval_token(case.principal or DEFAULT_PROFILE)


def _assert_known_principals(cases: Sequence[CoordinatorCase]) -> None:
    """A case naming a persona the minter does not know is an authoring bug;
    the run stops here instead of scoring that case unproven forever."""
    unknown = sorted(
        {case.case_id for case in cases if case.principal and case.principal not in PROFILES}
    )
    if unknown:
        raise ValueError(
            f"cases name principal profiles the eval minter does not know: {unknown}; "
            f"known profiles: {sorted(PROFILES)}"
        )


def _ask_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }


def _v2_ask_payload(
    question: str,
    *,
    thread_id: str | None = None,
    deadline_ms: int = TURN_DEADLINE_MS,
    run_id: str | None = None,
) -> dict[str, Any]:
    """A protocol version 2 ``new_question`` body; the run id doubles as the
    server correlation id, so it also joins the observation to backend traces."""
    return {
        "protocol_version": "2",
        "operation": "new_question",
        "run_id": run_id or uuid.uuid4().hex,
        "thread_id": thread_id or uuid.uuid4().hex,
        "question": question,
        "idempotency_key": uuid.uuid4().hex,
        "deadline_at_ms": int(time.time() * 1000) + deadline_ms,
        "response_policy": "allow_partial",
    }


def _citation_ids(body: dict[str, Any]) -> tuple[str, ...]:
    citations = body.get("citations")
    if not isinstance(citations, dict):
        return ()
    ids: list[str] = []
    for ref in citations.get("cited") or ():
        if isinstance(ref, dict) and ref.get("id"):
            ids.append(str(ref["id"]))
    return tuple(ids)


def _source_ids(body: dict[str, Any]) -> tuple[str, ...]:
    """Document source ids only. Exchange ids are lineage, not sources."""
    ids: list[str] = []
    for src in body.get("sources") or ():
        if isinstance(src, dict):
            ident = src.get("id") or src.get("source_file")
            if ident:
                ids.append(str(ident))
        elif isinstance(src, str):
            ids.append(src)
    ids.extend(_citation_ids(body))
    return tuple(ids)


def _has_document_evidence(body: dict[str, Any]) -> bool:
    sources = body.get("sources") or ()
    return bool(sources) or bool(_citation_ids(body))


_PUBLIC_ANSWER_FIELDS = (
    "answer",
    "question",
    "query_type",
    "answer_mode",
    "disambiguation",
    "business_query",
)


def _has_public_answer_fields(body: dict[str, Any]) -> bool:
    return any(body.get(key) not in (None, "", [], {}) for key in _PUBLIC_ANSWER_FIELDS)


def reconstruct_actions(body: dict[str, Any]) -> tuple[str, ...]:
    """Map public Answer fields onto coordinator steps. No invented actions key."""
    if body.get("disambiguation"):
        actions: list[str] = []
        query_type = body.get("query_type")
        if query_type in ("structured", "both") or body.get("business_query") is not None:
            actions.append("query_business")
        actions.append("clarify")
        return tuple(actions)
    if body.get("answer_mode") == "explanation":
        return ("explain_sources", "finish_answer")
    actions = []
    query_type = body.get("query_type")
    if query_type in ("structured", "both") or body.get("business_query") is not None:
        actions.append("query_business")
    if query_type in ("semantic", "both") and _has_document_evidence(body):
        actions.append("search_documents")
    actions.append("finish_answer")
    return tuple(actions)


def _compute_ttfa_from_capture(capture: Any, run_ids: tuple[str, ...]) -> int | None:
    events: list[dict[str, Any]] = []
    if isinstance(capture, (str, Path)):
        from scripts.eval.conversation_capture_status import load_events

        events = load_events(Path(capture))
    elif hasattr(capture, "path") and isinstance(getattr(capture, "path"), Path):
        from scripts.eval.conversation_capture_status import load_events

        events = load_events(capture.path)
    elif isinstance(capture, list):
        events = capture

    if not events:
        return None

    run_set = set(run_ids)
    turn_start_ns: int | None = None
    action_ns: int | None = None

    for ev in events:
        cid = ev.get("correlation_id")
        if run_set and cid not in run_set:
            continue
        phase = ev.get("phase")
        op = ev.get("operation")
        mono = ev.get("monotonic_ns")
        if mono is None:
            continue

        if turn_start_ns is None and (
            phase == "begin" and op in ("coordinator_decision", "run_coordinator_turn")
        ):
            turn_start_ns = mono

        if turn_start_ns is not None and action_ns is None:
            if (
                ev.get("admitted") is True
                or ev.get("action_kind") is not None
                or ev.get("malformed_kind") is not None
                or ev.get("stop_reason") is not None
                or (phase == "lifecycle" and ev.get("action"))
            ):
                action_ns = mono
                break

    if turn_start_ns is not None and action_ns is not None and action_ns >= turn_start_ns:
        return (action_ns - turn_start_ns) // 1_000_000
    return None


def _observation_from_answer(
    body: dict[str, Any],
    *,
    elapsed_ms: int,
    route_id: str,
    run_ids: tuple[str, ...] = (),
    thread_id: str | None = None,
    capture: Any = None,
    time_to_first_action_ms: int | None = None,
) -> CoordinatorRunOutput:
    if not _has_public_answer_fields(body):
        return _unproven(route_id, reason="response lacks public answer fields", run_ids=run_ids)

    evidence_records: list[Any] = []
    for rid in run_ids:
        evidence_records.extend(get_recorded_evidence(rid))

    if not evidence_records:
        missing_model_usage = True
        tokens = None
        time_to_first_token_ms = None
    else:
        missing_model_usage = False
        tokens = sum(
            (
                (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
                if isinstance(r, dict)
                else (
                    (getattr(r, "input_tokens", None) or 0)
                    + (getattr(r, "output_tokens", None) or 0)
                )
            )
            for r in evidence_records
        )
        time_to_first_token_ms = None
        for r in evidence_records:
            rec_ttft = r.get("ttft_ms") if isinstance(r, dict) else getattr(r, "ttft_ms", None)
            if rec_ttft is not None:
                time_to_first_token_ms = rec_ttft
                break

    ttfa: int | None = None
    if capture is not None:
        ttfa = _compute_ttfa_from_capture(capture, run_ids)
    if ttfa is None and time_to_first_action_ms is not None:
        ttfa = time_to_first_action_ms
    if ttfa is None:
        for r in evidence_records:
            rec_ttfa = (
                r.get("time_to_first_action_ms")
                if isinstance(r, dict)
                else getattr(r, "time_to_first_action_ms", None)
            )
            if rec_ttfa is None:
                rec_ttfa = r.get("ttfa_ms") if isinstance(r, dict) else getattr(r, "ttfa_ms", None)
            if rec_ttfa is not None:
                ttfa = rec_ttfa
                break
    if ttfa is None:
        ttfa = body.get("time_to_first_action_ms")

    return CoordinatorRunOutput(
        actions=reconstruct_actions(body),
        sources=_source_ids(body),
        answer_text=str(body.get("answer") or body.get("text") or ""),
        question_origin=body.get("question_origin"),
        time_to_first_token_ms=time_to_first_token_ms,
        time_to_first_action_ms=ttfa,
        latency_ms=body.get("latency_ms", elapsed_ms),
        tokens=tokens,
        capture_complete=True,
        missing_model_usage=missing_model_usage,
        route_id=route_id,
        model=body.get("model"),
        run_ids=run_ids,
        thread_id=thread_id,
    )


def _post_v2_ask(
    base: str,
    case: CoordinatorCase,
    payload: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    return _post_ask(f"{base}/api/ask", payload, _ask_headers(_bearer_for(case)), timeout=timeout)


def _returned_thread_id(body: dict[str, Any]) -> str | None:
    value = body.get("thread_id")
    return value if isinstance(value, str) and value else None


def execute_production_ask(
    case: CoordinatorCase,
    is_baseline: bool,
    *,
    capture: Any = None,
    time_to_first_action_ms: int | None = None,
    http_timeout_s: float = 30.0,
    deadline_ms: int = TURN_DEADLINE_MS,
    run_id: str | None = None,
) -> CoordinatorRunOutput:
    """POST protocol version 2 bodies. Coordinator on/off is process settings, not JSON.

    A case with history seeds the thread with its first history turn, then
    sends the question on the thread id the server returned.
    """
    route_id = BASELINE_ROUTE if is_baseline else CANDIDATE_ROUTE
    base = _eval_base_url(is_baseline)
    if not base:
        return _unproven(route_id, reason="no base URL for the route")

    run_ids: list[str] = []
    thread_id: str | None = None
    try:
        started = time.perf_counter()
        if case.context.history:
            seed_payload = _v2_ask_payload(
                case.context.history[0].user_text, deadline_ms=deadline_ms
            )
            run_ids.append(seed_payload["run_id"])
            thread_id = _returned_thread_id(
                _post_v2_ask(base, case, seed_payload, timeout=http_timeout_s)
            )
            if thread_id is None:
                return _unproven(
                    route_id, reason="seed turn returned no thread_id", run_ids=tuple(run_ids)
                )
        payload = _v2_ask_payload(
            case.context.question,
            thread_id=thread_id,
            deadline_ms=deadline_ms,
            run_id=run_id,
        )
        run_ids.append(payload["run_id"])
        body = _post_v2_ask(base, case, payload, timeout=http_timeout_s)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    except (
        OSError,
        TimeoutError,
        urllib.error.HTTPError,
        urllib.error.URLError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        # Transport and decoding failures of this one request; the row keeps
        # the request ids so the server capture can still be joined to it.
        return _unproven(
            route_id,
            reason=f"{type(exc).__name__}: {exc}",
            run_ids=tuple(run_ids),
            thread_id=thread_id,
        )

    return _observation_from_answer(
        body,
        elapsed_ms=elapsed_ms,
        route_id=route_id,
        run_ids=tuple(run_ids),
        thread_id=_returned_thread_id(body) or thread_id,
        capture=capture,
        time_to_first_action_ms=time_to_first_action_ms,
    )


def observe_coordinator_run(
    case: CoordinatorCase,
    is_baseline: bool,
    *,
    capture: Any = None,
    time_to_first_action_ms: int | None = None,
) -> CoordinatorRunOutput:
    """Call the production route when the exclusive env is ready; otherwise UNPROVEN."""
    ready, reason = exclusive_live_env_ready(is_baseline=is_baseline)
    if not ready:
        return _unproven(BASELINE_ROUTE if is_baseline else CANDIDATE_ROUTE, reason=reason)
    if capture is not None or time_to_first_action_ms is not None:
        return execute_production_ask(
            case,
            is_baseline,
            capture=capture,
            time_to_first_action_ms=time_to_first_action_ms,
        )
    return execute_production_ask(case, is_baseline)


def load_simple_bq_rb_cases() -> list[CoordinatorCase]:
    """Frozen simple-BQ rb-* cases from the living prod_ask_e2e suite."""
    suite = json.loads(SIMPLE_BQ_SUITE_PATH.read_text(encoding="utf-8"))
    loaded: list[CoordinatorCase] = []
    for raw in suite.get("cases", []):
        case_id = str(raw.get("id", ""))
        if not case_id.startswith("rb-"):
            continue
        if raw.get("expected_route") != "sql":
            continue
        oracle = raw.get("oracle") or {}
        value = oracle.get("value")
        loaded.append(
            CoordinatorCase(
                case_id=case_id,
                stratum="fresh_bq",
                context=CoordinatorContext(
                    turn_id=f"turn-{case_id}",
                    question=str(raw["question"]),
                ),
                required_actions=("query_business", "finish_answer"),
                expected_question_origin="user",
                answer_oracle=str(value) if value is not None else None,
                provenance=f"evals/prod_ask_e2e/suite.json:{case_id}",
            )
        )
    return loaded


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_build_hash() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    head = result.stdout.strip()
    if result.returncode == 0 and head:
        return head
    return "unknown"


def run_coordinator_eval(
    cases_path: Path | str,
    out_dir: Path | str,
    is_baseline: bool = False,
    only: str = "",
    execute_run: ExecuteRun | None = None,
    baseline_path: Path | str | None = None,
    *,
    arm: str = "current",
    deadline_ms: int = TURN_DEADLINE_MS,
    http_timeout_s: float = 30.0,
    campaign_cap_usd: Decimal | None = None,
    spend_ledger: Path | str | None = None,
    cell_ceiling_usd: Decimal | None = None,
    arm_wall_s: int = 7200,
    soft_alert_frac: float = SOFT_ALERT_FRAC,
) -> tuple[list[CaseResult], dict[str, Any]]:
    cases_file = Path(cases_path)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if is_baseline:
        source_cases = load_simple_bq_rb_cases()
        cases = [case for case in source_cases for _repeat in range(BASELINE_REPEATS)]
    else:
        all_cases = load_coordinator_cases(cases_file)
        _assert_known_principals(all_cases)
        if only:
            only_ids = {cid.strip() for cid in only.split(",") if cid.strip()}
            cases = [c for c in all_cases if c.case_id in only_ids]
        else:
            cases = all_cases

    live_spend = live_spend_required(
        live_env_flag=LIVE_ENV_FLAG,
        live_flag_value=os.environ.get(LIVE_ENV_FLAG),
        is_baseline=is_baseline,
    )
    spend_refusal = refuse_live_spend_start(
        required=live_spend,
        campaign_cap_usd=campaign_cap_usd,
        spend_ledger=spend_ledger,
    )
    if spend_refusal:
        raise ValueError(spend_refusal)

    spend_active = spend_ledger is not None and campaign_cap_usd is not None
    envelope = (
        SpendEnvelope(
            ledger=open_spend_ledger(spend_ledger, campaign_cap_usd=campaign_cap_usd),
            campaign_cap_usd=campaign_cap_usd,
            arm_wall_s=arm_wall_s,
            soft_alert_frac=soft_alert_frac,
            priced_cell=cell_ceiling_usd,
        )
        if spend_active
        else None
    )

    def _default_executor(case: CoordinatorCase, is_baseline_flag: bool) -> CoordinatorRunOutput:
        return observe_coordinator_run(case, is_baseline_flag)

    executor = execute_run or _default_executor
    env_ready, env_reason = exclusive_live_env_ready(is_baseline=is_baseline, arm=arm)
    build_hash = _repo_build_hash()
    cases_hash = _file_hash(cases_file) if cases_file.is_file() else ""
    suite_hash = _file_hash(SIMPLE_BQ_SUITE_PATH) if SIMPLE_BQ_SUITE_PATH.is_file() else ""

    results: list[CaseResult] = []
    traces: list[dict[str, Any]] = []
    latencies: list[int] = []
    ttfts: list[int] = []
    token_counts: list[int] = []
    strata_stats: dict[str, dict[str, int]] = {}
    incomplete = 0
    results_path = out_path / "results.jsonl"
    answers_path = out_path / "answers.jsonl"
    if envelope is not None and not is_baseline:
        answers_path.write_text("", encoding="utf-8")
    file_ctx = (
        contextlib.nullcontext(None) if is_baseline else results_path.open("w", encoding="utf-8")
    )

    def _append_trace(row: dict[str, Any], results_file: Any) -> None:
        if envelope is not None:
            case_ledger = envelope.last_slice or empty_case_ledger_slice()
            row = merge_case_ledger_into_row(row, case_ledger)
            append_answer_jsonl(answers_path, row, case_ledger.invocation_rows)
        traces.append(row)
        if results_file is not None:
            results_file.write(json.dumps(row) + "\n")
            results_file.flush()

    trace_ctx = TraceContext(build_hash=build_hash, cases_hash=cases_hash, suite_hash=suite_hash)
    total_n = len(cases)

    with file_ctx as results_file:
        for case in cases:
            if envelope is not None and envelope.abort_status is not None:
                break
            if envelope is not None and envelope.wall_exceeded():
                envelope.mark_wall_abort()
                break

            run_output: CoordinatorRunOutput
            reserved_run_id: str | None = None
            if envelope is not None:
                reserved_run_id, reserve_abort = envelope.reserve_next()
                if reserve_abort == "budget_exceeded" and reserved_run_id is not None:
                    run_output = _unproven(
                        CANDIDATE_ROUTE if not is_baseline else BASELINE_ROUTE,
                        reason="budget_exceeded",
                        run_ids=(reserved_run_id,),
                    )
                    case_res = score(case, run_output)
                    results.append(case_res)
                    incomplete += 1
                    _append_trace(build_trace_row(case_res, run_output, trace_ctx), results_file)
                    emit_heartbeat(
                        envelope, done_n=len(results), total_n=total_n, case_id=case.case_id
                    )
                    break

            if execute_run is None and not is_baseline and envelope is not None:
                run_output = execute_production_ask(
                    case,
                    is_baseline,
                    http_timeout_s=http_timeout_s,
                    deadline_ms=deadline_ms,
                    run_id=reserved_run_id,
                )
            else:
                run_output = executor(case, is_baseline)

            if envelope is not None:
                price_run_ids = run_output.run_ids or (
                    (reserved_run_id,) if reserved_run_id else ()
                )
                settle_status = envelope.settle_after_post(
                    price_run_ids,
                    reservation_id=reserved_run_id
                    or (price_run_ids[-1] if price_run_ids else None),
                    model=run_output.model,
                )
                if settle_status == "price_source_missing":
                    incomplete += 1
                run_output = apply_durable_usage(run_output, envelope.last_invocation_records)

            if not run_output.capture_complete:
                incomplete += 1
            if run_output.missing_model_usage and envelope is not None:
                envelope.missing_model_usage_count += 1
            if run_output.latency_ms is not None:
                latencies.append(run_output.latency_ms)
            if run_output.time_to_first_token_ms is not None:
                ttfts.append(run_output.time_to_first_token_ms)
            if run_output.tokens is not None:
                token_counts.append(run_output.tokens)

            case_res = score(case, run_output)
            results.append(case_res)
            row_ctx = TraceContext(
                build_hash=build_hash,
                cases_hash=cases_hash,
                suite_hash=suite_hash,
                reason=run_output.reason
                or (envelope.abort_reason if envelope is not None else None),
            )
            _append_trace(build_trace_row(case_res, run_output, row_ctx), results_file)
            emit_heartbeat(envelope, done_n=len(results), total_n=total_n, case_id=case.case_id)

            if envelope is not None and envelope.abort_status is not None:
                break

            st = case.stratum
            if st not in strata_stats:
                strata_stats[st] = {
                    "count": 0,
                    "trajectory_pass": 0,
                    "sources_pass": 0,
                    "grounding_pass": 0,
                    "unnecessary_calls": 0,
                }
            strata_stats[st]["count"] += 1
            if case_res.trajectory == "pass":
                strata_stats[st]["trajectory_pass"] += 1
            if case_res.sources == "pass":
                strata_stats[st]["sources_pass"] += 1
            if case_res.grounding == "pass":
                strata_stats[st]["grounding_pass"] += 1
            strata_stats[st]["unnecessary_calls"] += case_res.unnecessary_calls

    if envelope is not None:
        capture_status = envelope.capture_status(incomplete=incomplete)
    else:
        capture_status = "unproven" if incomplete else "complete"
    p50_lat = median(latencies)
    p95_lat = percentile(latencies, 0.95)
    p50_ttft = median(ttfts)
    median_tok = median(token_counts)

    if is_baseline:
        baseline_record: dict[str, Any] = {
            "build_hash": build_hash,
            "route_id": BASELINE_ROUTE,
            "coordinator_enabled": False,
            "repeats": BASELINE_REPEATS,
            "status": capture_status,
            "reason": env_reason if not env_ready else None,
            "suite_path": SIMPLE_BQ_SUITE_PATH.as_posix(),
            "suite_hash": suite_hash,
            "p50_latency_ms": p50_lat,
            "p95_latency_ms": p95_lat,
            "p50_time_to_first_token_ms": p50_ttft,
            "median_tokens": median_tok,
            "case_count": len(cases),
            "cases": [
                {
                    "case_id": r.case_id,
                    "latency_ms": r.latency_ms,
                    "tokens": r.tokens,
                    "ttft_ms": r.time_to_first_token_ms,
                }
                for r in results
            ],
        }
        (out_path / "baseline.json").write_text(
            json.dumps(baseline_record, indent=2), encoding="utf-8"
        )
        return results, baseline_record

    total = len(results)
    summary: dict[str, Any] = {
        "total_cases": total,
        "capture_status": capture_status,
        "reason": (
            envelope.abort_reason
            if envelope is not None and envelope.abort_reason
            else (env_reason if not env_ready else None)
        ),
        "build_hash": build_hash,
        "cases_hash": cases_hash,
        "route_id": CANDIDATE_ROUTE,
        "trajectory_pass_rate": (
            sum(1 for r in results if r.trajectory == "pass") / total if total > 0 else 0.0
        ),
        "sources_pass_rate": (
            sum(1 for r in results if r.sources == "pass") / total if total > 0 else 0.0
        ),
        "grounding_pass_rate": (
            sum(1 for r in results if r.grounding == "pass") / total if total > 0 else 0.0
        ),
        "total_unnecessary_calls": sum(r.unnecessary_calls for r in results),
        "invariant_violations": count_violations(results),
        "p50_latency_ms": p50_lat,
        "p95_latency_ms": p95_lat,
        "p50_time_to_first_token_ms": p50_ttft,
        "median_tokens": median_tok,
        "strata": strata_stats,
        "arm": arm,
    }
    if envelope is not None:
        summary.update(envelope.summary_fields())
    frozen_path = Path(baseline_path) if baseline_path else out_path / "baseline.json"
    if frozen_path.is_file():
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        if isinstance(frozen, dict):
            summary["baseline_compare"] = compare_baseline(frozen, summary)
    (out_path / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return results, summary


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    preset = ARM_PRESETS[args.arm]
    deadline_ms = args.deadline_ms if args.deadline_ms is not None else int(preset["deadline_ms"])
    http_timeout_s = (
        args.http_timeout_s if args.http_timeout_s is not None else float(preset["http_timeout_s"])
    )
    campaign_cap_usd = Decimal(args.campaign_cap_usd) if args.campaign_cap_usd is not None else None
    spend_ledger = Path(args.spend_ledger) if args.spend_ledger.strip() else None
    cell_ceiling_usd = Decimal(args.cell_ceiling_usd) if args.cell_ceiling_usd is not None else None
    if live_spend_required(
        live_env_flag=LIVE_ENV_FLAG,
        live_flag_value=os.environ.get(LIVE_ENV_FLAG),
        is_baseline=args.baseline,
    ):
        if campaign_cap_usd is None or spend_ledger is None:
            print(
                "live coordinator eval requires --campaign-cap-usd and --spend-ledger",
                file=sys.stderr,
            )
            return 1
        if not ledger_store_configured():
            print("query record DSN not configured for live spend", file=sys.stderr)
            return 1
    try:
        run_coordinator_eval(
            cases_path=args.cases,
            out_dir=args.out,
            is_baseline=args.baseline,
            only=args.only,
            baseline_path=args.baseline_file or None,
            arm=args.arm,
            deadline_ms=deadline_ms,
            http_timeout_s=http_timeout_s,
            campaign_cap_usd=campaign_cap_usd,
            spend_ledger=spend_ledger,
            cell_ceiling_usd=cell_ceiling_usd,
            arm_wall_s=args.arm_wall_s,
            soft_alert_frac=args.soft_alert_frac,
        )
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Error executing coordinator eval: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
