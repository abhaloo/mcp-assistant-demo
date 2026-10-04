"""Cross-domain benchmark harness: 32-case oracle, match, classify, Cube recording."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.business_query.definitions import current_bundle
from app.business_query.outcomes import AdapterUnsupported
from app.eval.business_query.cross_domain import (
    BENCH_DIR,
    AdapterCall,
    OracleResult,
    ScoringSpec,
    _abort_unless_cube_ready,
    _RecordingAdapter,
    assert_oracle_sql_unchanged,
    classify_outcome,
    expected_chain_cube_calls,
    load_cases,
    load_oracle_results,
    load_plans,
    load_scoring_specs,
    merge_companion_rows,
    values_match,
)

REPO_BENCH = Path(__file__).resolve().parents[2] / BENCH_DIR


def test_thirty_two_cases_plans_and_results_load() -> None:
    """Oracle: evals/business_query/cross_domain/*.jsonl — 37 cases, four of them anchors."""
    cases = load_cases(REPO_BENCH)
    plans = load_plans(REPO_BENCH)
    results = load_oracle_results(REPO_BENCH)
    anchors = {"xd-anchor-01", "xd-anchor-02", "xd-anchor-03", "xd-anchor-04"}
    assert anchors <= set(plans) == set(results) == {c.id for c in cases}
    assert len(cases) == 37 and len(plans) == 37 and len(results) == 37
    gaps = {c.id for c in cases if c.expected == "capability_gap"}
    assert gaps == {"xd-15", "xd-16", "xd-22", "xd-27"}
    specs = load_scoring_specs(REPO_BENCH)
    assert set(specs) <= {c.id for c in cases}
    for case_id, spec in specs.items():
        assert set(spec.compare_columns) <= set(results[case_id].columns)
        assert set(spec.column_map) <= set(results[case_id].columns)


def test_chain_cube_call_count_is_derived_from_plans_jsonl() -> None:
    """Oracle: G5 counts Cube *plans* the chain would send, not cases.

    Walk primary + companions (never alt). A plan counts when Cube would not
    raise AdapterUnsupported. Snapshot 2026-09-17 against live plans.jsonl:
    nine Cube-only cases (xd-08 has two plans) + one Cube plan in each mixed
    case (xd-12 primary, xd-20 primary, xd-21 companion) = 13.
    Subset asserts fail if the helper is a constant 13.
    xd-13, xd-14, and xd-23 validate as BusinessQueryPlan and count no Cube call.
    """
    from app.business_query.plan.query_plan import BusinessQueryPlan
    from app.eval.business_query.cross_domain import (
        cube_would_refuse,
        unvalidatable_primary_ids,
    )

    plans = load_plans(REPO_BENCH)
    bundle = current_bundle()
    invalid = unvalidatable_primary_ids(plans)
    assert invalid == set()
    for case_id in ("xd-13", "xd-14", "xd-23"):
        assert expected_chain_cube_calls({case_id: plans[case_id]}, bundle) == 0
    valid = {case_id: plan for case_id, plan in plans.items() if case_id not in invalid}
    assert expected_chain_cube_calls({"xd-08": plans["xd-08"]}, bundle) == 2
    assert expected_chain_cube_calls({"xd-12": plans["xd-12"]}, bundle) == 1
    assert expected_chain_cube_calls({"xd-27": plans["xd-27"]}, bundle) == 0
    assert expected_chain_cube_calls({"xd-03": plans["xd-03"]}, bundle) == 0
    assert cube_would_refuse(BusinessQueryPlan.model_validate(plans["xd-12"].companions[0]), bundle)
    assert expected_chain_cube_calls(valid, bundle) == 13


def test_oracle_sql_hashes_match_the_files() -> None:
    """Oracle: sql_sha256 in oracle-results.jsonl equals sha256 of oracle/<id>.sql."""
    assert_oracle_sql_unchanged(REPO_BENCH, load_oracle_results(REPO_BENCH))


def test_oracle_hash_mismatch_is_refused(tmp_path: Path) -> None:
    (tmp_path / "oracle").mkdir()
    (tmp_path / "oracle" / "xd-99.sql").write_text("SELECT 1\n", encoding="utf-8")
    bad = {
        "xd-99": OracleResult(
            id="xd-99",
            sql_sha256="0" * 64,
            database="mcp_local",
            business_date="2026-07-15",
            columns=["n"],
            row_count=1,
            rows=[[1]],
        )
    }
    with pytest.raises(ValueError, match="xd-99"):
        assert_oracle_sql_unchanged(tmp_path, bad)


def test_scalar_values_match_within_a_cent() -> None:
    """Oracle: xd-23 expects 1523.00 product units; Decimal from the driver counts."""
    oracle = OracleResult(
        id="xd-23",
        sql_sha256="x" * 64,
        database="mcp_local",
        business_date="2026-07-15",
        columns=["product_units_used"],
        row_count=1,
        rows=[[1523.0]],
    )
    ok, _ = values_match([{"product_units_used": 1523.004}], 1, oracle)
    assert ok
    ok, _ = values_match([{"product_units_used": Decimal("1523.004")}], 1, oracle)
    assert ok
    ok, detail = values_match([{"product_units_used": 1522.0}], 1, oracle)
    assert not ok and "1523.0" in detail
    ok, _ = values_match(
        [{"flag": True}],
        1,
        OracleResult(
            id="b",
            sql_sha256="x" * 64,
            database="mcp_local",
            business_date="2026-07-15",
            columns=["flag"],
            row_count=1,
            rows=[[1]],
        ),
    )
    assert not ok


def test_row_values_match_on_the_columns_the_spec_names() -> None:
    """Oracle: xd-14 expects order 744 with jobs 24693 and 24694; the answer lists jobs only,
    so the spec compares work_number and status."""
    oracle = OracleResult(
        id="xd-14",
        sql_sha256="x" * 64,
        database="mcp_local",
        business_date="2026-07-15",
        columns=["order_number", "job_id", "work_number", "status"],
        row_count=2,
        rows=[["744", 24693, "24693", "IN PROGRESS"], ["744", 24694, "24694", "IN PROGRESS"]],
    )
    spec = ScoringSpec(id="xd-14", compare_columns=["work_number", "status"])
    rows = [
        {"job.work_number": "24693", "job.status": "IN PROGRESS"},
        {"job.work_number": "24694", "job.status": "IN PROGRESS"},
    ]
    ok, _ = values_match(rows, 2, oracle, spec)
    assert ok
    ok, detail = values_match(rows, 2, oracle)
    assert not ok and "order_number" in detail
    ok, detail = values_match(rows[:1], 1, oracle, spec)
    assert not ok and "row_count" in detail


def test_truncated_answers_compare_their_rows_against_the_oracle() -> None:
    """Oracle: xd-24 returns 356 rows; the module returns at most 50, so every returned
    row must appear in the oracle and the total row count must match."""
    oracle = OracleResult(
        id="xd-24",
        sql_sha256="x" * 64,
        database="mcp_local",
        business_date="2026-07-15",
        columns=["order_number", "customer_name", "computed_status"],
        row_count=3,
        rows=[["005", "A", "IN_PROGRESS"], ["009", "B", "IN_PROGRESS"], ["010", "B", "NEW"]],
    )
    rows = [
        {
            "customer_order.order_number": "005",
            "customer_order.customer_name": "A",
            "customer_order.computed_status": "IN_PROGRESS",
        }
    ]
    ok, _ = values_match(rows, 3, oracle)
    assert ok
    bad_rows = [
        {
            "customer_order.order_number": "999",
            "customer_order.customer_name": "A",
            "customer_order.computed_status": "IN_PROGRESS",
        }
    ]
    ok, detail = values_match(bad_rows, 3, oracle)
    assert not ok and "999" in detail


def test_datetimes_match_their_iso_text() -> None:
    """Oracle: xd-13 records created_at as ISO text; the module returns a datetime."""
    from datetime import datetime

    oracle = OracleResult(
        id="xd-13",
        sql_sha256="x" * 64,
        database="mcp_local",
        business_date="2026-07-15",
        columns=["created_at"],
        row_count=1,
        rows=[["2026-07-15T10:01:28"]],
    )
    ok, _ = values_match([{"invoice.created_at": datetime(2026, 7, 15, 10, 1, 28)}], 1, oracle)
    assert ok


def test_null_oracle_cells_match_null_answer_cells() -> None:
    """Oracle: oracle-results.jsonl xd-31 groups jobs by order status and its first row is
    [null, 5201] (orphan jobs). The recorded cube run 20260917T214037Z returned exactly those
    four groups, so a NULL cell must count as a match, and a wrong NULL count must not."""
    oracle = OracleResult(
        id="xd-31",
        sql_sha256="x" * 64,
        database="mcp_local",
        business_date="2026-07-15",
        columns=["computed_status", "jobs_count"],
        row_count=4,
        rows=[[None, 5201], ["CANCELLED", 7], ["FINISHED", 269], ["IN_PROGRESS", 636]],
    )
    rows = [
        {"customer_order.computed_status": None, "jobs_count": 5201},
        {"customer_order.computed_status": "IN_PROGRESS", "jobs_count": 636},
        {"customer_order.computed_status": "FINISHED", "jobs_count": 269},
        {"customer_order.computed_status": "CANCELLED", "jobs_count": 7},
    ]
    ok, detail = values_match(rows, 4, oracle)
    assert ok, detail
    wrong = [{**rows[0], "jobs_count": 5200}, *rows[1:]]
    ok, detail = values_match(wrong, 4, oracle)
    assert not ok and "5201" in detail


def test_truncated_answers_ignore_their_own_null_cells() -> None:
    """Oracle: scoring-specs.jsonl xd-20 compares `name` only because quantity is NULL on job
    rows; a truncated answer row must not fail on a NULL cell the spec never asked about."""
    oracle = OracleResult(
        id="xd-20",
        sql_sha256="x" * 64,
        database="mcp_local",
        business_date="2026-07-15",
        columns=["kind", "name", "quantity"],
        row_count=3,
        rows=[["job", "A", None], ["job", "B", None], ["item", "C", 4.0]],
    )
    spec = ScoringSpec(id="xd-20", compare_columns=["name"])
    rows = [{"customer.name": "A", "work_order_item.quantity": None}]
    ok, detail = values_match(rows, 3, oracle, spec)
    assert ok, detail


def test_companion_rows_merge_by_a_shared_label_or_as_one_row() -> None:
    """Oracle: xd-08 (two scalars) and xd-12 (two tables keyed by customer name)."""
    assert merge_companion_rows([{"jobs_count": 28}], [{"invoices_count": 20}]) == [
        {"jobs_count": 28, "invoices_count": 20}
    ]
    merged = merge_companion_rows(
        [{"job.customer_name": "RIU SWAHILI", "jobs_count": 30}],
        [{"invoice.customer_name": "RIU SWAHILI", "invoiced_revenue": 31323987.5}],
    )
    assert merged == [
        {
            "job.customer_name": "RIU SWAHILI",
            "jobs_count": 30,
            "invoice.customer_name": "RIU SWAHILI",
            "invoiced_revenue": 31323987.5,
        }
    ]
    appended = merge_companion_rows([{"a": "x"}], [{"b": "y"}, {"b": "z"}])
    assert appended == [{"a": "x"}, {"b": "y"}, {"b": "z"}]


def test_classification_of_outcomes() -> None:
    """Oracle: spec §2 — answered first; a gap is only when nothing answered."""
    assert (
        classify_outcome(
            expected="capability_gap", plan_error=None, outcome_kind="unsupported", matched=None
        )
        == "capability_gap"
    )
    assert (
        classify_outcome(expected="answer", plan_error="invalid", outcome_kind=None, matched=None)
        == "invalid_plan"
    )
    assert (
        classify_outcome(
            expected="answer", plan_error=None, outcome_kind="unsupported", matched=None
        )
        == "engine_rule"
    )
    assert (
        classify_outcome(expected="answer", plan_error=None, outcome_kind="answered", matched=True)
        == "pass"
    )
    assert (
        classify_outcome(expected="answer", plan_error=None, outcome_kind="answered", matched=False)
        == "wrong_answer"
    )
    assert (
        classify_outcome(expected="answer", plan_error=None, outcome_kind=None, matched=None)
        == "no_plan"
    )


def test_classification_reports_a_wrong_answer_even_on_an_expected_gap() -> None:
    """Oracle: a wrong Cube/internal answer on xd-27 is not scored as a gap."""
    assert (
        classify_outcome(
            expected="capability_gap", plan_error=None, outcome_kind="answered", matched=False
        )
        == "wrong_answer"
    )


def test_adapters_used_names_the_adapter_that_answered_each_plan() -> None:
    """Oracle: Answered carries no adapter name (outcomes.py:283-304); the wrapper records it."""
    log: list[AdapterCall] = []

    class _Refuse:
        def execute(self, scoped, *, trace=None):
            raise AdapterUnsupported("unsupported_operator")

        def execute_with_evidence(self, scoped, *, trace=None):
            raise AdapterUnsupported("unsupported_operator")

    class _Answer:
        def execute(self, scoped, *, trace=None):
            return "answered"

        def execute_with_evidence(self, scoped, *, trace=None):
            return "answered"

    cube = _RecordingAdapter(_Refuse(), "cube", log)
    internal = _RecordingAdapter(_Answer(), "internal", log)
    with pytest.raises(AdapterUnsupported):
        cube.execute_with_evidence(None)
    assert internal.execute_with_evidence(None) == "answered"
    used = [c.name for c in log if c.answered]
    assert used == ["internal"]
    assert [c.name for c in log] == ["cube", "internal"]


def test_cube_calls_are_timed_per_call() -> None:
    """Oracle: G5 is p95 of cube_calls_ms, one number per Cube call that answered."""
    log: list[AdapterCall] = []

    class _SlowAnswer:
        def execute_with_evidence(self, scoped, *, trace=None):
            return "answered"

        def execute(self, scoped, *, trace=None):
            return "answered"

    _RecordingAdapter(_SlowAnswer(), "cube", log).execute_with_evidence(None)
    assert len(log) == 1 and log[0].name == "cube" and log[0].answered
    assert isinstance(log[0].elapsed_ms, int) and log[0].elapsed_ms >= 0


def test_incomplete_is_not_logged_as_a_cube_answer() -> None:
    """Oracle: Cube transport failure returns Incomplete; G5 counts only answers."""
    from app.business_query.outcomes import Incomplete

    log: list[AdapterCall] = []

    class _Died:
        def execute_with_evidence(self, scoped, *, trace=None):
            return Incomplete(reason_code="adapter_invalid")

        def execute(self, scoped, *, trace=None):
            return Incomplete(reason_code="adapter_invalid")

    result = _RecordingAdapter(_Died(), "cube", log).execute_with_evidence(None)
    assert result.outcome == "incomplete"
    assert log[0].answered is False
    assert log[0].incomplete is True
    assert [c.elapsed_ms for c in log if c.name == "cube" and c.answered] == []


def test_cube_run_aborts_when_the_api_reports_cube_not_ready(monkeypatch) -> None:
    """Oracle: a Cube outage is terminal; do not measure 32 incompletes as a run."""
    import asyncio

    import httpx

    from app.eval.business_query.cross_domain import run_engine_arm

    class _NotReady:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"checks": {"cube": False}}

    monkeypatch.setattr("httpx.get", lambda *a, **k: _NotReady())
    with pytest.raises(SystemExit) as err:
        asyncio.run(
            run_engine_arm(
                engine=object(),  # type: ignore[arg-type]
                bundle=current_bundle(),
                bench_dir=REPO_BENCH,
                adapter="cube",
                api_readyz="http://api/readyz",
            )
        )
    assert err.value.code == 2

    with pytest.raises(SystemExit) as missing:
        asyncio.run(
            run_engine_arm(
                engine=object(),  # type: ignore[arg-type]
                bundle=current_bundle(),
                bench_dir=REPO_BENCH,
                adapter="chain",
                api_readyz=None,
            )
        )
    assert missing.value.code == 2

    class _HttpFail:
        def raise_for_status(self) -> None:
            raise httpx.HTTPStatusError(
                "503",
                request=httpx.Request("GET", "http://api/readyz"),
                response=httpx.Response(503),
            )

        def json(self):
            return {"checks": {"cube": True}}

    monkeypatch.setattr("httpx.get", lambda *a, **k: _HttpFail())
    with pytest.raises(SystemExit) as http_err:
        _abort_unless_cube_ready("http://api/readyz")
    assert http_err.value.code == 2


def _stub_select_adapters(cube_inner, internal_inner=None):
    from app.eval.business_query.cross_domain import _RecordingAdapter

    def _select(*, adapter, log, **_k):
        cube = _RecordingAdapter(cube_inner, "cube", log)
        if adapter == "cube":
            return [cube]
        inner = internal_inner if internal_inner is not None else cube_inner
        return [cube, _RecordingAdapter(inner, "internal", log)]

    return _select


class _CubeIncomplete:
    def execute(self, scoped, *, trace=None):
        from app.business_query.outcomes import Incomplete

        return Incomplete(reason_code="adapter_invalid")

    def execute_with_evidence(self, scoped, *, trace=None):
        return self.execute(scoped, trace=trace)


class _CubeRefuse:
    def execute(self, scoped, *, trace=None):
        raise AdapterUnsupported("unsupported_operator")

    def execute_with_evidence(self, scoped, *, trace=None):
        return self.execute(scoped, trace=trace)


class _InternalIncomplete:
    def execute(self, scoped, *, trace=None):
        from app.business_query.outcomes import Incomplete

        return Incomplete(reason_code="adapter_invalid")

    def execute_with_evidence(self, scoped, *, trace=None):
        return self.execute(scoped, trace=trace)


def _patch_live_engine_arm(monkeypatch, cube_inner, internal_inner=None) -> None:
    from tests.business_query.test_composition import _fake_route

    monkeypatch.setattr(
        "app.eval.business_query.cross_domain._abort_unless_cube_ready", lambda *_a: None
    )
    monkeypatch.setattr(
        "app.eval.business_query.cross_domain._select_adapters",
        _stub_select_adapters(cube_inner, internal_inner),
    )
    monkeypatch.setattr(
        "app.business_query.composition.resolve_production_route",
        lambda *_a, **_k: _fake_route(request_timeout_s=120.0),
    )


def test_cube_arm_aborts_when_cube_returns_incomplete(monkeypatch) -> None:
    """Oracle: Cube Incomplete is terminal; abort uses the recording log, not detail text."""
    import asyncio

    import sqlalchemy as sa

    from app.eval.business_query.cross_domain import run_engine_arm

    _patch_live_engine_arm(monkeypatch, _CubeIncomplete())
    with pytest.raises(SystemExit) as err:
        asyncio.run(
            run_engine_arm(
                engine=sa.create_engine("sqlite:///:memory:"),
                bundle=current_bundle(),
                bench_dir=REPO_BENCH,
                only={"xd-01"},
                adapter="cube",
                api_readyz="http://api/readyz",
            )
        )
    assert err.value.code == 2


def test_chain_arm_does_not_abort_on_internal_incomplete(monkeypatch) -> None:
    """Oracle: chain continues when only the internal compiler returns Incomplete."""
    import asyncio

    import sqlalchemy as sa

    from app.eval.business_query.cross_domain import run_engine_arm

    _patch_live_engine_arm(monkeypatch, _CubeRefuse(), _InternalIncomplete())
    results = asyncio.run(
        run_engine_arm(
            engine=sa.create_engine("sqlite:///:memory:"),
            bundle=current_bundle(),
            bench_dir=REPO_BENCH,
            only={"xd-01"},
            adapter="chain",
            api_readyz="http://api/readyz",
        )
    )
    assert len(results) == 1
    assert results[0].classification == "engine_rule"
    assert results[0].detail.startswith("incomplete: adapter_invalid")


def test_alt_is_reported_as_its_own_row() -> None:
    """Oracle: xd-12 alt is G3 shape evidence; it never replaces the primary classification."""
    from app.eval.business_query.cross_domain import EngineArmResult, append_alt_row

    primary = EngineArmResult("xd-12", "engine_rule", "in_set", [], None, ["cube"], [], 1)
    alt = EngineArmResult("xd-12", "pass", "", [{"x": 1}], 1, ["cube"], [8], 1)
    rows = append_alt_row([primary], alt)
    assert rows[0].id == "xd-12" and rows[0].classification == "engine_rule"
    assert rows[1].id == "xd-12 (alt)" and rows[1].classification == "pass"


def test_diff_report_marks_disagreement() -> None:
    from app.eval.business_query.cross_domain import EngineArmResult, format_diff

    a = [EngineArmResult("xd-02", "pass", "", [{"n": 1}], 1, ["cube"], [10], 20)]
    b = [EngineArmResult("xd-02", "pass", "", [{"n": 2}], 1, ["internal"], [], 15)]
    text = format_diff({"cube": a, "internal": b})
    assert "xd-02" in text and "disagree" in text.casefold()
    assert "g6_both_answered=0" in text
    match = [{"n": 1}]
    agree = format_diff(
        {
            "cube": [EngineArmResult("xd-02", "pass", "", match, 1, ["cube"], [10], 20)],
            "internal": [EngineArmResult("xd-02", "pass", "", match, 1, ["internal"], [], 15)],
        }
    )
    assert "g6_both_answered=1" in agree


def test_diff_report_agrees_on_values_not_on_row_order_or_number_text() -> None:
    """Oracle: spec G6 says both-pass arms must agree on values. Recorded runs
    20260917T214037Z-cube / 20260917T214103Z-internal: xd-17 rows are identical but Cube
    orders by the measure and the internal compiler by the name (the plan has no order);
    xd-02 has_orders comes back "1" from Cube and 1 from the internal compiler."""
    from app.eval.business_query.cross_domain import EngineArmResult, format_diff, rows_agree

    cube_17 = [
        {"job.department_name": "FINISHING", "product_units_used": Decimal("49640.00")},
        {"job.department_name": "SALES", "product_units_used": Decimal("25.00")},
    ]
    internal_17 = [
        {"product_units_used": Decimal("25.00"), "job.department_name": "SALES"},
        {"product_units_used": Decimal("49640.00"), "job.department_name": "FINISHING"},
    ]
    cube_02 = [{"customer.has_orders": "1", "invoiced_revenue": Decimal("756860107.9484999")}]
    internal_02 = [{"invoiced_revenue": Decimal("756860107.9484999"), "customer.has_orders": 1}]
    text = format_diff(
        {
            "cube": [
                EngineArmResult("xd-17", "pass", "", cube_17, 2, ["cube"], [41], 45),
                EngineArmResult("xd-02", "pass", "", cube_02, 1, ["cube"], [62], 65),
            ],
            "internal": [
                EngineArmResult("xd-17", "pass", "", internal_17, 2, ["internal"], [], 16),
                EngineArmResult("xd-02", "pass", "", internal_02, 1, ["internal"], [], 66),
            ],
        }
    )
    assert "g6_both_answered=2" in text and "disagree" not in text.casefold()
    assert not rows_agree([{"n": 1}], [{"n": 2}])
    assert not rows_agree([{"n": 1}], [{"m": 1}])
    assert not rows_agree([{"n": 1}], [{"n": 1}, {"n": 1}])


def test_swapped_columns_do_not_match() -> None:
    """Oracle: resume design section 3, comparator probe A — the same two numbers in the
    wrong columns are a wrong answer."""
    oracle = OracleResult(
        id="p",
        sql_sha256="x",
        database="d",
        business_date="2026-07-15",
        columns=["invoice_id", "jobs_count"],
        rows=[[7, 3]],
        row_count=1,
    )
    ok, _ = values_match([{"invoice_id": 3, "jobs_count": 7}], 1, oracle)
    assert ok is False


def test_an_answer_row_satisfies_at_most_one_oracle_row() -> None:
    """Oracle: resume design section 3, comparator probe B — a duplicated oracle row needs
    two matching answer rows; an unrelated row does not stand in."""
    oracle = OracleResult(
        id="p",
        sql_sha256="x",
        database="d",
        business_date="2026-07-15",
        columns=["a", "b"],
        rows=[[1, "x"], [1, "x"]],
        row_count=2,
    )
    ok, _ = values_match([{"a": 1, "b": "x"}, {"a": 99, "b": "zzz"}], 2, oracle)
    assert ok is False


def test_a_spec_column_missing_from_the_answer_fails() -> None:
    """Oracle: resume design section 3 outcome 8 — a compared column the answer never
    produced is a failure, not a skipped check."""
    oracle = OracleResult(
        id="p",
        sql_sha256="x",
        database="d",
        business_date="2026-07-15",
        columns=["invoice_number", "work_number"],
        rows=[["E7418", "24622"]],
        row_count=1,
    )
    spec = ScoringSpec(id="p", compare_columns=["invoice_number", "work_number"])
    ok, reason = values_match([{"work_number": "24622"}], 1, oracle, spec)
    assert ok is False
    assert "invoice_number" in reason


def test_a_column_map_names_where_an_aliased_oracle_column_lives() -> None:
    """Oracle: oracle/xd-30.sql aliases job.id as job_id; the answer key is job.id."""
    oracle = OracleResult(
        id="p",
        sql_sha256="x",
        database="d",
        business_date="2026-07-15",
        columns=["job_id", "status"],
        rows=[[24622, "FINISHED"]],
        row_count=1,
    )
    spec = ScoringSpec(
        id="p", compare_columns=["job_id", "status"], column_map={"job_id": "job.id"}
    )
    ok, _ = values_match([{"job.id": 24622, "job.status": "FINISHED"}], 1, oracle, spec)
    assert ok is True
    ok, reason = values_match([{"job.id": 24622, "job.status": "FINISHED"}], 1, oracle)
    assert ok is False and "job_id" in reason


def test_two_suffix_matches_without_a_map_are_ambiguous() -> None:
    """Oracle: xd-12 merged rows carry job.customer_name and invoice.customer_name."""
    oracle = OracleResult(
        id="p",
        sql_sha256="x",
        database="d",
        business_date="2026-07-15",
        columns=["customer_name"],
        rows=[["ZK VENTURES"]],
        row_count=1,
    )
    row = {"job.customer_name": "ZK VENTURES", "invoice.customer_name": "ZK VENTURES"}
    ok, reason = values_match([row], 1, oracle)
    assert ok is False and "ambiguous" in reason


def test_prefixed_answer_keys_match_bare_oracle_columns() -> None:
    """Oracle: recorded run xd-26 (`jobs_count`) and oracle-results xd-14 (`work_number`):
    answer keys may carry the resource prefix the oracle omits."""
    oracle = OracleResult(
        id="p",
        sql_sha256="x",
        database="d",
        business_date="2026-07-15",
        columns=["work_number", "status"],
        rows=[["24693", "IN PROGRESS"]],
        row_count=1,
    )
    ok, _ = values_match([{"job.work_number": "24693", "job.status": "IN PROGRESS"}], 1, oracle)
    assert ok is True


@pytest.mark.integration
def test_negative_control_inner_join_fails_anchors_without_children(monkeypatch) -> None:
    """MariaDB: INNER JOIN on xd-anchor-01 drops childless parents so identity is 0."""
    import asyncio

    import sqlalchemy as sa

    from app.business_query.compile.statement_builder import parse_on_sql
    from app.business_query.definitions import current_bundle
    from app.business_query.outcomes import PlanRefused
    from app.eval.business_query.cross_domain import load_plans, run_engine_arm
    from app.eval.business_query.harness import billing_engine

    def force_inner_child(adapter, anchor, join_tree, tables, lookup_forced):
        from_clause: sa.sql.FromClause = tables[anchor]
        joined = {anchor}
        pending = list(join_tree)
        while pending:
            progressed = False
            for join in list(pending):
                if join.from_resource in joined and join.to_resource not in joined:
                    target = join.to_resource
                elif join.to_resource in joined and join.from_resource not in joined:
                    target = join.from_resource
                else:
                    continue
                on_clause = sa.and_(parse_on_sql(join, tables), *lookup_forced.get(target, []))
                from_clause = from_clause.join(tables[target], on_clause, isouter=False)
                joined.add(target)
                pending.remove(join)
                progressed = True
            if not progressed:
                raise PlanRefused("no_join_path")
        return from_clause

    monkeypatch.setattr(
        "app.business_query.compile.statement_builder.assemble_from_clause",
        force_inner_child,
    )
    engine = billing_engine("mcp_analytics_w2")
    results = asyncio.run(
        run_engine_arm(
            engine=engine,
            bundle=current_bundle(),
            bench_dir=REPO_BENCH,
            only={"xd-anchor-01"},
            adapter="internal",
        )
    )
    assert results, "xd-anchor-01 must run"
    row = results[0]
    assert any(
        "invoice.id" in candidate or "invoice.invoice_number" in candidate for candidate in row.rows
    )
    job_key = "job.id" if row.rows and "job.id" in row.rows[0] else "job.work_number"
    assert all(candidate.get(job_key) is not None for candidate in row.rows)
    authored = load_plans(REPO_BENCH)["xd-anchor-01"]
    assert authored.plan is not None
    assert authored.plan.get("anchor") == "invoice"


# oracle/rc-01.sql against mcp_analytics_w2_recover (LIMIT 5).
_RC01 = [
    ["E6358", "Neptune Pwani Beach Resort & Spa", 15812500.0],
    ["E6370", "UNICEF", 14202500.0],
    ["E6407", "UNICEF", 10350000.0],
    ["E6353", "ZANZIBAR PORTS CORPORATION (ZPC)", 9631250.0],
    ["E6354", "ZANZIBAR PORTS CORPORATION (ZPC)", 9487500.0],
]


def _rc01_oracle() -> OracleResult:
    return OracleResult(
        id="rc-01",
        sql_sha256="0" * 64,
        database="mcp_analytics_w2_recover",
        business_date="2026-07-15",
        columns=["invoice_number", "customer_name", "items_total"],
        row_count=5,
        rows=[list(row) for row in _RC01],
    )


def _rc01_engine_rows() -> list[dict]:
    return [
        {
            "invoice.invoice_number": number,
            "invoice.customer_name": customer,
            "invoice.items_total": str(total),
        }
        for number, customer, total in _RC01
    ]


def test_a_top_n_case_is_scored_on_the_rows_it_returned() -> None:
    """Oracle: oracle/rc-01.sql returns the five highest rows (LIMIT 5); the
    engine returned the same five and counted 77 matching invoices before its
    own limit (the merged internal run of the live acceptance)."""
    spec = load_scoring_specs(BENCH_DIR)["rc-01"]
    assert values_match(_rc01_engine_rows(), 77, _rc01_oracle(), spec) == (True, "")


def test_a_list_case_still_fails_when_the_total_differs() -> None:
    """Guard: a spec without the top-N mode keeps the total check."""
    ok, detail = values_match(_rc01_engine_rows(), 77, _rc01_oracle(), ScoringSpec(id="xd-list"))
    assert ok is False
    assert detail == "row_count 77 != oracle 5"
