"""Paired timing probe for capability-card position (human vs system).

model    production planner (spends; --confirm-spend required)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_QUESTIONS: tuple[str, ...] = (
    "what are our latest invoices and what are their job details",
    "add dates",
    "Invoice issue date",
    "at least 10",
    "what are our latest invoices and their jobs",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--confirm-spend", action="store_true", default=False)
    parser.add_argument("--questions", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    return parser


def load_questions(path: Path | None) -> list[str]:
    if path is None:
        return list(DEFAULT_QUESTIONS)
    rows: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw: Any = json.loads(line)
        if isinstance(raw, str):
            rows.append(raw)
        else:
            rows.append(str(raw["question"]))
    return rows


def _capability_card() -> str:
    from app.auth import Principal
    from app.business_query.authorize import capability_card
    from app.business_query.definitions import current_bundle

    bundle = current_bundle()
    grants = sorted({perm for entry in bundle.capabilities for perm in entry.required_permissions})
    principal = Principal(user_id="probe", role="probe", permissions=grants)
    return capability_card(principal, bundle)


def _plan_fingerprint(outcome: object) -> str | None:
    from app.business_query.plan.query_plan import BusinessQueryPlan, plan_fingerprint

    if isinstance(outcome, BusinessQueryPlan):
        return plan_fingerprint(outcome)
    return None


async def run_probe(questions: list[str], out_dir: Path) -> Path:
    from app.business_query.plan.llm_planner import LlmPlanner
    from app.business_query.wire.trace import QueryTrace
    from app.config import settings

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "probe.jsonl"
    card = _capability_card()
    with dest.open("w", encoding="utf-8", newline="\n") as handle:
        for question in questions:
            for flag in (False, True):
                settings.planner_card_in_system_message = flag
                trace = QueryTrace()
                started = time.perf_counter()
                outcome = await LlmPlanner(trace=trace).plan(question, card)
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
                row = {
                    "question": question,
                    "flag": flag,
                    "ttft_ms": trace.first_progress_ms,
                    "latency_ms": trace.planner_ms if trace.planner_ms is not None else latency_ms,
                    "input_tokens": trace.tokens_prompt,
                    "cached_tokens": None,
                    "plan_fingerprint": _plan_fingerprint(outcome),
                }
                handle.write(json.dumps(row, default=str) + "\n")
    return dest


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_spend:
        print(
            "the card-position probe spends; pass --confirm-spend",
            file=sys.stderr,
        )
        return 2
    if args.out is None:
        print("--out is required when spending", file=sys.stderr)
        return 2
    dest = asyncio.run(run_probe(load_questions(args.questions), args.out))
    print(f"written: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
