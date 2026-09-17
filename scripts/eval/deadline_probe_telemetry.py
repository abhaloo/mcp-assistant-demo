"""Durable token/TTFT/SQL timing + answers.jsonl for the deadline probe.

Live HTTP scoring cannot read the canary in-process evidence map. This module
joins Postgres ledger rows once per case: invocations, SQL timings, refs, and
usage. Spend charges from that slice. The runner merges refs and writes
answers.jsonl from it. SQL statement text never enters the artifact.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.eval.conversation_coordinator import CoordinatorRunOutput
from app.experiments.core.answer_persistence import sanitize_answer_row_for_persistence
from app.telemetry.invocation_ledger import ledger_store_configured

PRICE_POLL_ATTEMPTS = 10
PRICE_POLL_SLEEP_S = 0.2


@dataclass(frozen=True)
class DurableUsage:
    tokens: int | None
    input_tokens: int
    output_tokens: int
    ttft_ms: int | None
    missing_model_usage: bool


@dataclass(frozen=True)
class CaseLedgerSlice:
    """One case's durable join: invocation rows, SQL timings, refs, and usage."""

    invocation_rows: tuple[Any, ...]
    sql_rows: tuple[Any, ...]
    refs: dict[str, Any]
    usage: DurableUsage


def _int_field(row: Any, name: str) -> int:
    raw = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
    return int(raw or 0)


def usage_from_records(records: list[Any] | tuple[Any, ...]) -> DurableUsage:
    """Sum input+output tokens; first non-null ttft_ms. Empty records are missing."""
    if not records:
        return DurableUsage(
            tokens=None,
            input_tokens=0,
            output_tokens=0,
            ttft_ms=None,
            missing_model_usage=True,
        )
    input_tokens = sum(_int_field(row, "input_tokens") for row in records)
    output_tokens = sum(_int_field(row, "output_tokens") for row in records)
    ttft_ms = None
    for row in records:
        raw = row.get("ttft_ms") if isinstance(row, dict) else getattr(row, "ttft_ms", None)
        if raw is not None:
            ttft_ms = int(raw)
            break
    return DurableUsage(
        tokens=input_tokens + output_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        ttft_ms=ttft_ms,
        missing_model_usage=False,
    )


def is_zero_llm_greeting(model: str | None) -> bool:
    """Greeting turns record model ``none`` and have no LLM rows: tokens=0, not missing."""
    return model == "none"


def apply_durable_usage(output: CoordinatorRunOutput, records: list[Any]) -> CoordinatorRunOutput:
    """Copy ledger usage onto an HTTP observation. Greeting turns with no LLM are complete."""
    usage = usage_from_records(records)
    if usage.missing_model_usage and is_zero_llm_greeting(output.model):
        return output.model_copy(
            update={"missing_model_usage": False, "tokens": 0, "time_to_first_token_ms": None}
        )
    if usage.missing_model_usage:
        return output
    return output.model_copy(
        update={
            "missing_model_usage": False,
            "tokens": usage.tokens,
            "time_to_first_token_ms": usage.ttft_ms,
        }
    )


def extract_answer_text(records: list[Any] | tuple[Any, ...]) -> str:
    """Last coordinator finish_answer block text. Conversation rewrites are not the HTTP answer."""
    spoken = ""
    for row in records:
        purpose = row.get("purpose") if isinstance(row, dict) else getattr(row, "purpose", None)
        content = (
            row.get("response_content")
            if isinstance(row, dict)
            else getattr(row, "response_content", None)
        )
        if purpose != "coordinator" or not isinstance(content, str) or not content.strip():
            continue
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if not isinstance(item, dict) or item.get("name") != "finish_answer":
                continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            blocks = args.get("blocks") if isinstance(args.get("blocks"), list) else []
            parts = [
                block["text"].strip()
                for block in blocks
                if isinstance(block, dict)
                and isinstance(block.get("text"), str)
                and block["text"].strip()
            ]
            if parts:
                spoken = "\n\n".join(parts)
    return spoken


def extract_sql_elapsed_ms(rows: list[Any] | tuple[Any, ...]) -> list[int]:
    elapsed: list[int] = []
    for row in rows:
        raw = row.get("elapsed_ms") if isinstance(row, dict) else getattr(row, "elapsed_ms", None)
        if raw is None:
            continue
        elapsed.append(int(raw))
    return elapsed


def _int_ids(rows: list[Any] | tuple[Any, ...]) -> list[int]:
    ids: list[int] = []
    for row in rows:
        raw = getattr(row, "id", None)
        if raw is None and isinstance(row, dict):
            raw = row.get("id")
        if raw is None:
            continue
        ids.append(int(raw))
    return ids


def empty_durable_refs() -> dict[str, Any]:
    return {
        "model_invocation_ids": [],
        "sql_execution_ids": [],
        "sql_elapsed_ms": [],
        "durable_input_tokens": 0,
        "durable_output_tokens": 0,
        "durable_ttft_ms": None,
    }


def empty_case_ledger_slice() -> CaseLedgerSlice:
    return CaseLedgerSlice(
        invocation_rows=(),
        sql_rows=(),
        refs=empty_durable_refs(),
        usage=usage_from_records(()),
    )


def rows_for_run_ids(rows: list[Any], run_ids: tuple[str, ...]) -> list[Any]:
    wanted = set(run_ids)
    matched: list[Any] = []
    for row in rows:
        cid = (
            row.get("correlation_id")
            if isinstance(row, dict)
            else getattr(row, "correlation_id", None)
        )
        if cid in wanted:
            matched.append(row)
    return matched


def durable_refs_from_rows(invocation_rows: list[Any], sql_rows: list[Any]) -> dict[str, Any]:
    usage = usage_from_records(invocation_rows)
    return {
        "model_invocation_ids": _int_ids(invocation_rows),
        "sql_execution_ids": _int_ids(sql_rows),
        "sql_elapsed_ms": extract_sql_elapsed_ms(sql_rows),
        "durable_input_tokens": usage.input_tokens,
        "durable_output_tokens": usage.output_tokens,
        "durable_ttft_ms": usage.ttft_ms,
    }


def slice_from_rows(invocation_rows: list[Any], sql_rows: list[Any]) -> CaseLedgerSlice:
    refs = durable_refs_from_rows(invocation_rows, sql_rows)
    return CaseLedgerSlice(
        invocation_rows=tuple(invocation_rows),
        sql_rows=tuple(sql_rows),
        refs=refs,
        usage=usage_from_records(invocation_rows),
    )


def apply_usage_to_trace_row(row: dict[str, Any], records: list[Any] | tuple[Any, ...]) -> None:
    if row.get("tokens") is not None:
        return
    usage = usage_from_records(records)
    if not usage.missing_model_usage:
        row["tokens"] = usage.tokens
        row["time_to_first_token_ms"] = usage.ttft_ms
    elif is_zero_llm_greeting(row.get("model") if isinstance(row.get("model"), str) else None):
        row["tokens"] = 0


def merge_case_ledger_into_row(row: dict[str, Any], ledger: CaseLedgerSlice) -> dict[str, Any]:
    """Copy a trace row and add durable ids, SQL timings, and usage from one join."""
    merged = {**row, **ledger.refs}
    apply_usage_to_trace_row(merged, ledger.invocation_rows)
    if not (merged.get("answer_text") or merged.get("answer")):
        recovered = extract_answer_text(ledger.invocation_rows)
        if recovered:
            merged["answer_text"] = recovered
    return merged


@asynccontextmanager
async def _eval_ledger_resources():
    if not ledger_store_configured():
        yield
        return
    from app.resources import ProcessResources

    async with ProcessResources.from_settings():
        yield


async def _poll_invocations(run_ids: tuple[str, ...]) -> list[Any]:
    from app.telemetry.invocation_ledger import query_durable_invocation_evidence

    collected: list[Any] = []
    for run_id in run_ids:
        found: list[Any] = []
        for _attempt in range(PRICE_POLL_ATTEMPTS):
            found = list(await query_durable_invocation_evidence(run_id))
            if found:
                break
            await asyncio.sleep(PRICE_POLL_SLEEP_S)
        if not found:
            return []
        collected.extend(found)
    return collected


async def collect_case_ledger_slice(
    run_ids: tuple[str, ...], *, poll: bool = False
) -> CaseLedgerSlice:
    """One IN-query join for invocations and SQL timings. No SQL statement text."""
    from app.telemetry.invocation_ledger import (
        query_durable_invocation_evidence_for_correlations,
        query_durable_sql_timings_for_correlations,
    )

    if not run_ids:
        return empty_case_ledger_slice()
    if poll:
        invocation_rows = await _poll_invocations(run_ids)
        if not invocation_rows:
            return empty_case_ledger_slice()
    else:
        invocation_rows = list(await query_durable_invocation_evidence_for_correlations(run_ids))
    sql_rows = list(await query_durable_sql_timings_for_correlations(run_ids))
    return slice_from_rows(invocation_rows, sql_rows)


def load_case_ledger_slice(run_ids: tuple[str, ...], *, poll: bool = False) -> CaseLedgerSlice:
    """Join Ask run_ids to refs plus invocation rows. No SQL bodies in the refs.

    ``poll=True`` waits for every run_id to yield invocation rows (live settle).
    An empty member after the poll budget is a missing price source.
    """
    if not run_ids:
        return empty_case_ledger_slice()
    if not poll and not ledger_store_configured():
        return empty_case_ledger_slice()
    return asyncio.run(_load_case_ledger_slice_async(run_ids, poll=poll))


async def _load_case_ledger_slice_async(run_ids: tuple[str, ...], *, poll: bool) -> CaseLedgerSlice:
    async with _eval_ledger_resources():
        return await collect_case_ledger_slice(run_ids, poll=poll)


def attach_durable_refs(row: dict[str, Any]) -> dict[str, Any]:
    """Copy a trace row and add durable ids, SQL timings, and usage when jsonl lacks them."""
    raw_ids = row.get("run_ids") or ()
    run_ids = tuple(str(item) for item in raw_ids)
    ledger = load_case_ledger_slice(run_ids)
    return merge_case_ledger_into_row(row, ledger)


def enrich_results_jsonl(path: Path) -> int:
    """Rewrite a probe jsonl with durable telemetry and write answers.jsonl beside it."""
    if not path.is_file():
        return 0
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return 0
    return asyncio.run(_enrich_results_jsonl_async(path, lines))


async def _enrich_results_jsonl_async(path: Path, lines: list[str]) -> int:
    from app.telemetry.invocation_ledger import (
        query_durable_invocation_evidence_for_correlations,
        query_durable_sql_timings_for_correlations,
    )

    parsed = [json.loads(line) for line in lines]
    all_ids = tuple(
        dict.fromkeys(str(item) for row in parsed for item in (row.get("run_ids") or ()))
    )
    all_inv: list[Any] = []
    all_sql: list[Any] = []
    if ledger_store_configured() and all_ids:
        async with _eval_ledger_resources():
            all_inv = list(await query_durable_invocation_evidence_for_correlations(all_ids))
            all_sql = list(await query_durable_sql_timings_for_correlations(all_ids))

    answers_path = path.parent / "answers.jsonl"
    enriched: list[str] = []
    answers: list[str] = []
    for row in parsed:
        run_ids = tuple(str(item) for item in (row.get("run_ids") or ()))
        records = rows_for_run_ids(all_inv, run_ids)
        sql_rows = rows_for_run_ids(all_sql, run_ids)
        ledger = slice_from_rows(records, sql_rows)
        row = merge_case_ledger_into_row(row, ledger)
        answers.append(json.dumps(build_probe_answer_row(row, list(ledger.invocation_rows))))
        enriched.append(json.dumps(row))
    path.write_text("\n".join(enriched) + ("\n" if enriched else ""), encoding="utf-8")
    answers_path.write_text("\n".join(answers) + ("\n" if answers else ""), encoding="utf-8")
    return len(enriched)


def _reasoning_text(records: list[Any] | tuple[Any, ...]) -> str | None:
    for row in records:
        purpose = row.get("purpose") if isinstance(row, dict) else getattr(row, "purpose", None)
        content = (
            row.get("reasoning_content")
            if isinstance(row, dict)
            else getattr(row, "reasoning_content", None)
        )
        if isinstance(content, str) and content.strip():
            return content
        if purpose == "record_reasoning":
            response = (
                row.get("response_content")
                if isinstance(row, dict)
                else getattr(row, "response_content", None)
            )
            if isinstance(response, str) and response.strip():
                return response
    return None


def build_probe_answer_row(
    trace: dict[str, Any],
    records: list[Any] | tuple[Any, ...],
    *,
    sql_elapsed_ms: tuple[int, ...] | list[int] = (),
) -> dict[str, Any]:
    """Campaign answers.jsonl row: tokens, TTFT, SQL timings, sanitized reasoning."""
    usage = usage_from_records(records)
    elapsed = [int(v) for v in sql_elapsed_ms] or list(trace.get("sql_elapsed_ms") or [])
    raw = {
        "case_id": trace.get("case_id"),
        "arm": trace.get("arm") or "current",
        "answer": trace.get("answer_text") or trace.get("answer") or extract_answer_text(records),
        "model": trace.get("model"),
        "run_ids": list(trace.get("run_ids") or ()),
        "model_invocation_ids": list(trace.get("model_invocation_ids") or ()),
        "sql_execution_ids": list(trace.get("sql_execution_ids") or ()),
        "sql_elapsed_ms": elapsed,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "time_to_first_token_ms": usage.ttft_ms,
        "latency_ms": trace.get("latency_ms"),
        "reasoning_text": _reasoning_text(records),
    }
    return sanitize_answer_row_for_persistence(raw)


def append_answer_jsonl(
    path: Path, trace: dict[str, Any], records: list[Any] | tuple[Any, ...]
) -> None:
    """Append one sanitized Campaign answer row. Creates the file if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(build_probe_answer_row(trace, records)) + "\n")
