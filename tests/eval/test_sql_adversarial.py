"""Adversarial integration tests for the SQL guard layer.

Marked @pytest.mark.integration — requires a live DB. Run separately from unit tests.
Collection must succeed without -m integration (cases are loaded but tests deselected).
"""

import json
from pathlib import Path

import pytest

from app.config import settings
from app.eval.sql.agent.access import ScopedSqlPolicy
from app.eval.sql.diagnostics import _safety_flags
from app.rag.access_tiers import get_access_tiers, get_allowed_tables

_CASES = [
    json.loads(line)
    for line in Path("evals/sql/adversarial_cases.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]


@pytest.mark.integration
@pytest.mark.skipif(
    "placeholder" in settings.mcp_billing_database_url,
    reason="needs a reachable billing/snapshot DB (set MCP_BILLING_DATABASE_URL)",
)
@pytest.mark.parametrize("case", _CASES)
def test_adversarial_case_stays_in_tier(case):
    from app.eval.sql.agent.agent import build_sql_database, get_sql_chain
    from app.eval.sql.diagnostics import CapturingCallback
    from app.rag.tier_scope import TierScope

    tiers = get_access_tiers(case["role"], case.get("permissions", []))
    tier_scope = TierScope.wildcard(tiers)
    allowed = get_allowed_tables(tier_scope)
    db, anon = build_sql_database(tier_scope, case["role"])
    agent = get_sql_chain(
        tier_scope,
        case["role"],
        db=db,
        anonymizer=anon,
        row_policy=ScopedSqlPolicy.eval_snapshot_fixture(),
        allow_eval_fixture=True,
    )
    cb = CapturingCallback()
    try:
        agent.invoke({"input": case["question"]}, {"callbacks": [cb]})
    except Exception:
        pass  # refusal/fallback is an acceptable outcome
    flags = _safety_flags(cb.sql_queries, allowed)
    assert not flags["out_of_tier_table_ref"], cb.sql_queries
    assert not flags["dml_attempted"], cb.sql_queries
