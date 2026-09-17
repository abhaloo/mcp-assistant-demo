"""In-memory cache of canonical gold query results for the SQL eval harness.

Populated once after freeze gold verification. Never persisted to disk — only
row tuples keyed by case id, gold SQL hash, and ordered flag.
"""

from __future__ import annotations

import hashlib
from typing import Any

_GOLD_CACHE: dict[tuple[str, str, bool], tuple[tuple, ...]] | None = None


def _cache_key(case_id: str, gold_sql: str, *, ordered: bool) -> tuple[str, str, bool]:
    gold_hash = hashlib.sha256(gold_sql.encode("utf-8")).hexdigest()
    return case_id, gold_hash, ordered


def build_gold_cache(
    cases: list[dict[str, Any]],
    *,
    fetch_fn: Any,
) -> dict[tuple[str, str, bool], tuple[tuple, ...]]:
    """Execute every gold query once and return an in-memory cache."""
    from app.eval.sql.agent.access import ScopedSqlPolicy, ensure_scoped_sql_access
    from app.eval.sql.agent.agent import build_sql_database
    from app.rag.access_tiers import get_access_tiers

    ensure_scoped_sql_access(ScopedSqlPolicy.eval_snapshot_fixture())
    cache: dict[tuple[str, str, bool], tuple[tuple, ...]] = {}
    for case in cases:
        case_id = str(case["id"])
        gold_sql = case["gold_sql"]
        ordered = bool(case.get("ordered"))
        key = _cache_key(case_id, gold_sql, ordered=ordered)
        tiers = get_access_tiers(case["role"], case.get("permissions", []))
        db, _ = build_sql_database(tiers, case["role"])
        rows = fetch_fn(db, gold_sql)
        cache[key] = tuple(tuple(row) for row in rows)
    return cache


def install_gold_cache(cache: dict[tuple[str, str, bool], tuple[tuple, ...]] | None) -> None:
    """Install or clear the process-local gold cache."""
    global _GOLD_CACHE
    _GOLD_CACHE = cache


def cached_gold_rows(
    case_id: str,
    gold_sql: str,
    *,
    ordered: bool | None,
) -> tuple[tuple, ...] | None:
    """Return cached gold rows when the cache is warm; otherwise None."""
    if _GOLD_CACHE is None:
        return None
    is_ordered = bool(ordered)
    key = _cache_key(case_id, gold_sql, ordered=is_ordered)
    hit = _GOLD_CACHE.get(key)
    if hit is not None:
        return hit
    return None
