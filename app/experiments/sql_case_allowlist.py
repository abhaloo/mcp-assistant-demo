"""Single allowlist loader and partitioner for SQL holdout canaries and case filters."""

from __future__ import annotations

import copy
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.paths import REPO_ROOT

FLIP_CONTRACT_PATH = REPO_ROOT / "evals" / "experiments" / "sql-flip-shape-date-p0-contract.json"
RESIDUAL_CONTRACT_PATH = (
    REPO_ROOT / "evals" / "experiments" / "sql-post-canary-residual-canary.json"
)

# Tip flips only (frozen in contract). Do not expand without contract revision.
FLIP_CANARY_CASE_IDS: tuple[str, ...] = (
    "inv-fully-paid-count",
    "inv-overpaid-count",
    "inv-top-customer",
    "quote-largest",
    "prod-largest-invoice",
    "prod-jobs-no-invoice",
    "orders-open",
)

RESIDUAL_CANARY_CASE_IDS: tuple[str, ...] = (
    "prod-revenue-june9",
    "cust-no-orders",
    "fin-ar-aging",
)


def load_case_allowlist(
    contract_path: Path,
    *,
    pinned: tuple[str, ...],
) -> tuple[str, ...]:
    """Load allowlist from Success Contract; fall back to pinned constant."""
    if not contract_path.is_file():
        return pinned
    doc = json.loads(contract_path.read_text(encoding="utf-8"))
    return tuple(str(x) for x in doc["canary_allowlist"])


def select_sql_cases_by_ids(
    cases: Sequence[dict[str, Any]],
    case_ids: Sequence[str],
) -> list[dict[str, Any]]:
    """Return cases in allowlist order. Fail closed on missing or extras-not-requested."""
    by_id = {str(case["id"]): case for case in cases}
    missing = [cid for cid in case_ids if cid not in by_id]
    if missing:
        raise ValueError(f"case_id allowlist missing from stage cases: {missing}")
    return [by_id[cid] for cid in case_ids]


def expected_cell_count(
    allowlist: Sequence[str] | int,
    *,
    arms: int = 2,
    repeats: int = 5,
) -> int:
    count = allowlist if isinstance(allowlist, int) else len(allowlist)
    return count * arms * repeats


def load_residual_clarify_answer_overrides(*, contract_path: Path | None = None) -> dict[str, str]:
    """Residual-only oracle replies — never written into shared evals/sql/cases.jsonl."""
    path = contract_path or RESIDUAL_CONTRACT_PATH
    if not path.is_file():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw = doc.get("clarify_answer_overrides") or {}
    return {str(k): str(v) for k, v in raw.items()}


def apply_clarify_answer_overrides(
    cases: list[dict[str, Any]],
    overrides: dict[str, str],
) -> list[dict[str, Any]]:
    """Return copied cases with residual-only clarify_answer overlays applied."""
    if not overrides:
        return cases
    out: list[dict[str, Any]] = []
    for case in cases:
        cid = str(case.get("id") or "")
        if cid in overrides:
            patched = copy.deepcopy(case)
            patched["clarify_answer"] = overrides[cid]
            out.append(patched)
        else:
            out.append(case)
    return out
