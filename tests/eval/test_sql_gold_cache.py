"""Tests for in-memory SQL gold result cache."""

from __future__ import annotations

from app.eval.sql.diagnostics import score_case
from app.eval.sql.gold_cache import (
    build_gold_cache,
    cached_gold_rows,
    install_gold_cache,
)


def test_score_case_uses_cached_gold_without_refetch() -> None:
    fetch_calls = {"n": 0}
    gold_rows = [(1, "Alice"), (2, "Bob")]
    cache = {
        ("case-1", __import__("hashlib").sha256(b"SELECT id, name FROM t").hexdigest(), False): (
            (1, "Alice"),
            (2, "Bob"),
        )
    }
    install_gold_cache(cache)
    try:

        def fetch(_db, _sql):
            fetch_calls["n"] += 1
            return gold_rows

        scored = score_case(
            db=object(),
            agent_sql=None,
            gold_sql="SELECT id, name FROM t",
            ordered=False,
            case_id="case-1",
        )
        assert fetch_calls["n"] == 0
        assert scored["reason"] == "no_agent_query"
        assert scored["gold_row_count"] == 2
    finally:
        install_gold_cache(None)


def test_cached_gold_preserves_ordered_vs_unordered_semantics() -> None:
    """Ordered and unordered flags are distinct cache keys."""
    gold_sql = "SELECT id FROM t ORDER BY id"
    ordered_rows = ((1,), (2,))
    unordered_rows = ((2,), (1,))
    install_gold_cache(
        {
            ("c1", __import__("hashlib").sha256(gold_sql.encode()).hexdigest(), True): ordered_rows,
            ("c1", __import__("hashlib").sha256(gold_sql.encode()).hexdigest(), False): (
                unordered_rows
            ),
        }
    )
    try:
        assert cached_gold_rows("c1", gold_sql, ordered=True) == ordered_rows
        assert cached_gold_rows("c1", gold_sql, ordered=False) == unordered_rows
    finally:
        install_gold_cache(None)


def test_build_gold_cache_executes_once_per_case() -> None:
    calls: list[str] = []

    def fake_fetch(_db, sql: str) -> list[tuple]:
        calls.append(sql)
        return [(1,)]

    cases = [
        {"id": "a", "role": "admin", "permissions": [], "gold_sql": "SELECT 1", "ordered": False},
        {"id": "b", "role": "admin", "permissions": [], "gold_sql": "SELECT 2", "ordered": True},
    ]

    class FakeDb:
        def _execute(self, *_args, **_kwargs):
            raise AssertionError("build_gold_cache must use fetch_fn, not db directly")

    import app.eval.sql.agent.agent as sql_chain
    import app.rag.access_tiers as access_tiers

    original_build = sql_chain.build_sql_database
    original_tiers = access_tiers.get_access_tiers

    sql_chain.build_sql_database = lambda tiers, role: (FakeDb(), None)
    access_tiers.get_access_tiers = lambda role, perms: ["all"]

    try:
        cache = build_gold_cache(cases, fetch_fn=fake_fetch)
    finally:
        sql_chain.build_sql_database = original_build
        access_tiers.get_access_tiers = original_tiers

    assert calls == ["SELECT 1", "SELECT 2"]
    assert len(cache) == 2
