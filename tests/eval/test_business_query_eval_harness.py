"""Harness product seams: behaviour-miss, total_count, arm, run-kind, excluded."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.business_query.seal.events import (
    BusinessQueryExecutionEvent,
    EventPayloadMode,
    ResultMember,
)
from app.eval.business_query.readiness import EvaluationReadinessError, select_run_cases
from app.eval.business_query.score_resolved import (
    _score_answered_total_count,
    score_resolved_execution,
)
from scripts.eval import business_query_eval_run as eval_run
from scripts.eval.business_query_eval_run import build_eval_module


def _stub_event(*, rows, total_row_count, truncated, members):
    started = datetime(2026, 8, 13, tzinfo=UTC)
    member_names = tuple(members)
    return BusinessQueryExecutionEvent.create(
        answer_query_id="aq_event",
        project_id="eval-project",
        adapter="internal",
        backend="mysql",
        plan_fingerprint="a" * 64,
        compiled_query_digest="b" * 64,
        parameter_scope_digest="c" * 64,
        bundle_hash="d" * 64,
        manifest_hash="e" * 64,
        result_members=tuple(ResultMember(name=name, value_kind="string") for name in member_names),
        result_rows=tuple(rows),
        returned_row_count=len(rows),
        total_row_count=total_row_count,
        truncated=truncated,
        database_identity="eval-db",
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        retention_at=started + timedelta(days=7),
        payload_mode=EventPayloadMode.ENCRYPTED,
        case_id="bq-15",
    )


def _stub_resolver():
    class Resolver:
        async def resolve(self, answer_query_id, access, *, now=None):
            return _stub_event(
                rows=[{"n": "1"}],
                total_row_count=1,
                truncated=False,
                members=("n",),
            )

    return Resolver()


def _minimal_preflight():
    return {
        "arm": {
            "route_id": "openai-luna-plan",
            "provider": "openai",
            "deployment": "gpt-5.4-mini",
            "structured_output_mode": "json_schema",
            "reasoning_effort": "medium",
        }
    }


@pytest.mark.asyncio
async def test_behaviour_only_answered_scores_as_miss_not_abort():
    case = {"id": "bq-16", "draft_expected": "clarification_required"}
    run = {
        "outcome": "answered",
        "rows": [{"bill_outstanding": 1.0}],
        "receipt": {"answer_query_id": "aq-1"},
        "total_row_count": 333,
        "truncated": True,
        "result_member_schema": ("bill_outstanding",),
    }
    score = await score_resolved_execution(case, None, None, run, resolver=None, access=None)
    assert score.passed is False
    assert "expected clarification_required" in score.detail


@pytest.mark.asyncio
async def test_answered_expected_without_oracle_still_aborts():
    case = {"id": "bq-99", "draft_expected": "answered"}
    run = {"outcome": "answered", "receipt": {"answer_query_id": "aq-9"}}
    with pytest.raises(EvaluationReadinessError):
        await score_resolved_execution(
            case, None, None, run, resolver=_stub_resolver(), access=None
        )


def test_total_count_scoring_uses_event_rows_and_reported_total():
    case = {"id": "bq-15", "draft_expected": "answered", "scoring": "total_count"}
    oracle = {"columns": ["customers_never_ordered"], "row_count": 1, "rows": [[597]]}
    event = _stub_event(
        rows=[{"customer.name": "x"}],
        total_row_count=597,
        truncated=True,
        members=("customer.name",),
    )
    score = _score_answered_total_count(case, oracle, event)
    assert score.passed is True


def test_resolve_run_kind_allows_paid_diagnostic():
    from app.eval.business_query.contract import resolve_run_kind

    assert (
        resolve_run_kind(smoke=False, confirm_spend=True, run_kind_flag="diagnostic")
        == "diagnostic"
    )
    assert resolve_run_kind(smoke=False, confirm_spend=True, run_kind_flag="gate") == "gate"


def test_evidence_context_accepts_dict_arm_and_rejects_string_arm():
    preflight_ok = {**_minimal_preflight(), "causal_arm": "module-candidate"}
    eval_run._evidence_context(run_id="r", case_id="bq-01", repeat_index=0, preflight=preflight_ok)
    preflight_bad = {**_minimal_preflight(), "arm": "module-candidate"}
    with pytest.raises((TypeError, KeyError)):
        eval_run._evidence_context(
            run_id="r", case_id="bq-01", repeat_index=0, preflight=preflight_bad
        )


def test_select_run_cases_drops_excluded_and_rejects_only_excluded():
    cases = {
        "bq-01": {"id": "bq-01"},
        "bq-07": {"id": "bq-07", "eval_status": "excluded"},
    }
    selected = select_run_cases(cases, only=None)
    assert set(selected) == {"bq-01"}
    with pytest.raises(SystemExit, match="excluded"):
        select_run_cases(cases, only={"bq-07"})


def test_main_calls_select_run_cases():
    import inspect

    assert "select_run_cases" in inspect.getsource(eval_run.main)


def test_eval_module_uses_default_presenter():
    from unittest.mock import MagicMock

    from app.business_query.wire.comparison_presenter import present_for_plan
    from app.business_query.wire.trace import QueryTrace
    from tests.contracts import v2_principal

    module = build_eval_module(
        principal=v2_principal(),
        engine=MagicMock(),
        bundle=MagicMock(content_hash="sha256:" + "c" * 64),
        recorder=lambda _label: None,
        trace=QueryTrace(),
    )
    assert module._presenter is present_for_plan


def test_eval_module_wires_sql_value_resolver():
    """Oracle: D-S1-LOOKUP wiring into build_eval_module."""
    from unittest.mock import MagicMock

    from app.business_query.plan.value_resolver.sql import SqlValueResolver
    from app.business_query.wire.trace import QueryTrace
    from tests.contracts import v2_principal

    module = build_eval_module(
        principal=v2_principal(),
        engine=MagicMock(),
        bundle=MagicMock(content_hash="sha256:" + "c" * 64),
        recorder=lambda _label: None,
        trace=QueryTrace(),
    )
    assert isinstance(module._value_resolver, SqlValueResolver)


@pytest.mark.asyncio
async def test_run_one_records_resolver_query_id(monkeypatch):
    """Oracle: eval harness captures resolver_query_id from module outcomes."""
    from app.eval.business_query.run_support import run_one
    from tests.contracts import answered_outcome, business_query_receipt

    receipt = business_query_receipt(resolver_query_id="rq_test123")
    fake_outcome = answered_outcome(receipt=receipt)

    class StubModule:
        async def query(self, *args, **kwargs):
            return fake_outcome

    monkeypatch.setattr(
        "app.eval.business_query.run_support.build_eval_module",
        lambda **kwargs: StubModule(),
    )

    case = {"id": "bq-15", "question": "test"}
    result = await run_one(case, None, None, lambda _l: None)
    assert result["resolver_query_id"] == "rq_test123"
