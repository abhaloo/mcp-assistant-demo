"""Eval-case access — THE definition of what each eval kind includes."""

from __future__ import annotations

import json
from typing import Literal

from app.paths import REPO_ROOT

DATASET_PATH = REPO_ROOT / "tests" / "eval_dataset.json"

EXCLUDE_PREFIXES: tuple[str, ...] = ("access-control-", "sql-", "router-")

EvalKind = Literal["semantic", "structured", "router", "all"]


def load_eval_cases(kind: EvalKind = "all", subset: list[str] | None = None) -> list[dict]:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    if kind == "semantic":
        data = [
            c for c in data if not c["id"].startswith(EXCLUDE_PREFIXES) and c.get("expected_answer")
        ]
    elif kind == "structured":
        data = [c for c in data if c["id"].startswith("sql-")]
    elif kind == "router":
        data = [c for c in data if c["id"].startswith("router-")]
    if subset is not None:
        wanted = set(subset)
        data = [c for c in data if c["id"] in wanted]
    return data
