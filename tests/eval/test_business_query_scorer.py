from datetime import datetime
from decimal import Decimal

import pytest

from app.eval.business_query.scorer import (
    CaseScoringSpec,
    MemberMapping,
    ValueKind,
    score_case,
    values_match,
)


def _oracle(rows, columns=("v",)):
    return {"columns": list(columns), "rows": rows, "row_count": len(rows)}


def _spec(
    *mappings,
    row_key=(),
    row_count=1,
    total_row_count=1,
    truncated=False,
    ordered=False,
    metadata_allowlist=(),
):
    return CaseScoringSpec(
        member_mappings=tuple(mappings),
        row_key=tuple(row_key),
        expected_row_count=row_count,
        expected_total_row_count=total_row_count,
        expected_truncated=truncated,
        ordered=ordered,
        metadata_allowlist=frozenset(metadata_allowlist),
    )


class TestFrozenCaseScoringSpec:
    def test_equal_values_under_the_wrong_members_fail(self):
        oracle = _oracle([["12.00", "0.00"]], columns=("total", "tax"))
        spec = _spec(
            MemberMapping("total", "bill.total", ValueKind.CURRENCY),
            MemberMapping("tax", "bill.tax", ValueKind.CURRENCY),
        )

        assert not values_match(
            oracle,
            [{"bill.total": "0.00", "bill.tax": "12.00"}],
            spec=spec,
            total_row_count=1,
            truncated=False,
        )

    def test_currency_that_differs_by_exactly_one_cent_fails(self):
        oracle = _oracle([[Decimal("12.00")]], columns=("total",))
        spec = _spec(MemberMapping("total", "bill.total", ValueKind.CURRENCY))

        assert not values_match(
            oracle,
            [{"bill.total": Decimal("12.01")}],
            spec=spec,
            total_row_count=1,
            truncated=False,
        )

    def test_sub_cent_currency_values_that_quantize_to_the_same_cent_match(self):
        oracle = _oracle([[Decimal("12.001")]], columns=("total",))
        spec = _spec(MemberMapping("total", "bill.total", ValueKind.CURRENCY))

        assert values_match(
            oracle,
            [{"bill.total": Decimal("12.004")}],
            spec=spec,
            total_row_count=1,
            truncated=False,
        )

    def test_non_money_numbers_compare_exactly_after_normalization(self):
        oracle = _oracle([[Decimal("12")]], columns=("jobs",))
        spec = _spec(MemberMapping("jobs", "job.count", ValueKind.NON_MONEY))

        assert not values_match(
            oracle,
            [{"job.count": Decimal("12.009")}],
            spec=spec,
            total_row_count=1,
            truncated=False,
        )

    def test_unordered_rows_match_by_the_frozen_row_key(self):
        oracle = _oracle([[1, "Alpha"], [2, "Beta"]], columns=("id", "name"))
        spec = _spec(
            MemberMapping("id", "customer.id", ValueKind.NON_MONEY),
            MemberMapping("name", "customer.name", ValueKind.NON_MONEY),
            row_key=("id",),
            row_count=2,
            total_row_count=2,
        )

        assert values_match(
            oracle,
            [
                {"customer.id": 2, "customer.name": "Beta"},
                {"customer.id": 1, "customer.name": "Alpha"},
            ],
            spec=spec,
            total_row_count=2,
            truncated=False,
        )

    def test_two_oracle_columns_cannot_map_to_one_result_member(self):
        with pytest.raises(ValueError, match="result mappings must be unique"):
            _spec(
                MemberMapping("orders", "result.count", ValueKind.NON_MONEY),
                MemberMapping("jobs", "result.count", ValueKind.NON_MONEY),
            )

    def test_duplicate_oracle_columns_are_refused(self):
        with pytest.raises(ValueError, match="oracle mappings must be unique"):
            _spec(
                MemberMapping("total", "bill.total", ValueKind.CURRENCY),
                MemberMapping("total", "bill.tax", ValueKind.CURRENCY),
            )

    def test_missing_member_mappings_are_refused(self):
        with pytest.raises(ValueError, match="requires named member mappings"):
            _spec()

    def test_oracle_column_without_a_mapping_fails_closed(self):
        oracle = _oracle([["12.00", "0.00"]], columns=("total", "tax"))
        spec = _spec(MemberMapping("total", "bill.total", ValueKind.CURRENCY))

        assert not values_match(
            oracle,
            [{"bill.total": "12.00"}],
            spec=spec,
            total_row_count=1,
            truncated=False,
        )

    def test_extra_result_member_without_allowlist_fails_closed(self):
        oracle = _oracle([[24693]], columns=("job_id",))
        spec = _spec(MemberMapping("job_id", "job.id", ValueKind.NON_MONEY))

        assert not values_match(
            oracle,
            [{"job.id": 24693, "job.status": "IN PROGRESS"}],
            spec=spec,
            actual_member_schema=("job.id", "job.status"),
            total_row_count=1,
            truncated=False,
        )

    def test_pre_frozen_metadata_member_is_allowed(self):
        oracle = _oracle([[24693]], columns=("job_id",))
        spec = _spec(
            MemberMapping("job_id", "job.id", ValueKind.NON_MONEY),
            metadata_allowlist=("job.status",),
        )

        assert values_match(
            oracle,
            [{"job.id": 24693, "job.status": "IN PROGRESS"}],
            spec=spec,
            total_row_count=1,
            truncated=False,
        )

    def test_empty_rows_without_an_actual_member_schema_fail_closed(self):
        oracle = _oracle([], columns=("job_id",))
        spec = _spec(
            MemberMapping("job_id", "job.id", ValueKind.NON_MONEY),
            row_count=0,
            total_row_count=0,
        )

        assert not values_match(
            oracle,
            [],
            spec=spec,
            total_row_count=0,
            truncated=False,
        )

    def test_multi_row_grain_without_a_row_key_fails_closed(self):
        with pytest.raises(ValueError, match="multi-row scoring spec requires a row key"):
            _spec(
                MemberMapping("id", "customer.id", ValueKind.NON_MONEY),
                row_count=2,
                total_row_count=2,
            )

    def test_duplicate_row_key_fails_even_when_visible_cardinality_matches(self):
        oracle = _oracle([[1, "Alpha"], [2, "Beta"]], columns=("id", "name"))
        spec = _spec(
            MemberMapping("id", "customer.id", ValueKind.NON_MONEY),
            MemberMapping("name", "customer.name", ValueKind.NON_MONEY),
            row_key=("id",),
            row_count=2,
            total_row_count=2,
        )

        assert not values_match(
            oracle,
            [
                {"customer.id": 1, "customer.name": "Alpha"},
                {"customer.id": 1, "customer.name": "Alpha"},
            ],
            spec=spec,
            total_row_count=2,
            truncated=False,
        )

    def test_missing_row_fails_visible_cardinality(self):
        oracle = _oracle([[1], [2]], columns=("id",))
        spec = _spec(
            MemberMapping("id", "customer.id", ValueKind.NON_MONEY),
            row_key=("id",),
            row_count=2,
            total_row_count=2,
        )

        assert not values_match(
            oracle,
            [{"customer.id": 1}],
            spec=spec,
            total_row_count=2,
            truncated=False,
        )

    def test_extra_row_fails_visible_cardinality(self):
        oracle = _oracle([[1], [2]], columns=("id",))
        spec = _spec(
            MemberMapping("id", "customer.id", ValueKind.NON_MONEY),
            row_key=("id",),
            row_count=2,
            total_row_count=2,
        )

        assert not values_match(
            oracle,
            [{"customer.id": 1}, {"customer.id": 2}, {"customer.id": 3}],
            spec=spec,
            total_row_count=3,
            truncated=False,
        )

    def test_ordered_result_rejects_a_row_permutation(self):
        oracle = _oracle([[1, "Alpha"], [2, "Beta"]], columns=("id", "name"))
        spec = _spec(
            MemberMapping("id", "customer.id", ValueKind.NON_MONEY),
            MemberMapping("name", "customer.name", ValueKind.NON_MONEY),
            row_key=("id",),
            row_count=2,
            total_row_count=2,
            ordered=True,
        )

        assert not values_match(
            oracle,
            [
                {"customer.id": 2, "customer.name": "Beta"},
                {"customer.id": 1, "customer.name": "Alpha"},
            ],
            spec=spec,
            total_row_count=2,
            truncated=False,
        )


class TestTimestampsCompareAcrossTheJsonBoundary:
    """The answer key round-trips through JSON, so a timestamp arrives as a
    string; the module returns a live datetime. Comparing them as-is failed
    every correct date answer in the 2026-08-11 run (bq-01/02/03)."""

    def test_live_datetime_matches_isoformat_string_from_the_key(self):
        oracle = _oracle([["2026-07-15T09:42:25"]], columns=("last_order",))
        assert values_match(oracle, [{"ordered_at": datetime(2026, 7, 15, 9, 42, 25)}])

    def test_space_separated_timestamp_matches_t_separated(self):
        oracle = _oracle([["2026-07-15T09:42:25"]], columns=("last_order",))
        assert values_match(oracle, [{"ordered_at": "2026-07-15 09:42:25"}])

    def test_fractional_seconds_do_not_break_the_match(self):
        oracle = _oracle([["2026-07-15T09:42:25"]], columns=("last_order",))
        assert values_match(oracle, [{"ordered_at": "2026-07-15 09:42:25.000000"}])

    def test_a_genuinely_different_timestamp_still_fails(self):
        oracle = _oracle([["2026-07-15T09:42:25"]], columns=("last_order",))
        assert not values_match(oracle, [{"ordered_at": datetime(2026, 7, 10, 7, 25, 36)}])

    def test_a_different_time_on_the_same_day_still_fails(self):
        oracle = _oracle([["2026-07-15T09:42:25"]], columns=("last_order",))
        assert not values_match(oracle, [{"ordered_at": datetime(2026, 7, 15, 18, 0, 0)}])


class TestValuesMatch:
    def test_exact_scalar_matches(self):
        assert values_match(_oracle([[502]]), [{"open_orders": 502}])

    def test_scalar_within_money_tolerance_matches(self):
        # The oracle rounds to 2dp; the module may carry more precision.
        assert values_match(_oracle([[93869715.77]]), [{"invoiced_revenue": 93869715.7712}])

    def test_scalar_outside_tolerance_fails(self):
        assert not values_match(_oracle([[93869715.77]]), [{"invoiced_revenue": 80725839.80}])

    def test_column_names_are_ignored_only_values_compared(self):
        # Oracle aliases and bundle measure names differ; values are the contract.
        assert values_match(_oracle([[597]]), [{"totally_different_name": 597}])

    def test_row_order_ignored_by_default(self):
        oracle = _oracle([["a", 1], ["b", 2]], columns=("k", "n"))
        assert values_match(oracle, [{"k": "b", "n": 2}, {"k": "a", "n": 1}])

    def test_row_order_enforced_when_ordered(self):
        oracle = _oracle([["a", 1], ["b", 2]], columns=("k", "n"))
        assert not values_match(oracle, [{"k": "b", "n": 2}, {"k": "a", "n": 1}], ordered=True)

    def test_row_count_mismatch_fails(self):
        assert not values_match(_oracle([[1], [2]]), [{"n": 1}])

    def test_strings_compared_case_and_space_insensitively(self):
        # Real data carries stray whitespace ('Sea Cliff Resort & Spa ').
        assert values_match(
            _oracle([["Sea Cliff Resort & Spa "]]), [{"c": "sea cliff resort & spa"}]
        )

    def test_empty_oracle_matches_empty_rows(self):
        assert values_match(_oracle([]), [])


class TestScoreCase:
    def test_answered_case_without_an_independent_oracle_fails_closed(self):
        case = {"id": "bq-06", "draft_expected": "answered"}

        score = score_case(case, None, "answered", [{"open_orders": 502}])

        assert not score.passed
        assert score.detail == "answered case lacks an independent oracle"

    def test_answered_gate_case_without_a_frozen_spec_fails_closed(self):
        case = {"id": "bq-06", "draft_expected": "answered"}

        score = score_case(
            case,
            _oracle([[502]]),
            "answered",
            [{"open_orders": 502}],
            total_row_count=1,
            gate=True,
        )

        assert not score.literal_match
        assert not score.passed
        assert score.detail == "missing frozen CaseScoringSpec"

    def test_answered_with_matching_literal_passes(self):
        case = {"id": "bq-06", "draft_expected": "answered"}
        score = score_case(case, _oracle([[502]]), "answered", [{"n": 502}])
        assert score.outcome_match and score.literal_match and score.passed

    def test_answered_with_wrong_literal_fails(self):
        case = {"id": "bq-06", "draft_expected": "answered"}
        score = score_case(case, _oracle([[502]]), "answered", [{"n": 4}])
        assert score.outcome_match
        assert not score.passed
        assert score.literal_match is False
        assert score.detail == "values differ from answer key (1 expected row(s))"

    def test_behavioural_case_scores_on_outcome_only(self):
        case = {"id": "bq-19", "draft_expected": "unsupported"}
        score = score_case(case, None, "unsupported", None)
        assert score.passed
        assert score.literal_match is None

    def test_wrong_outcome_fails_even_with_right_values(self):
        case = {"id": "bq-17", "draft_expected": "clarification_required"}
        score = score_case(case, _oracle([[502]]), "answered", [{"n": 502}])
        assert not score.passed
        assert score.literal_match is True
        assert score.detail == "expected clarification_required, got answered"

    def test_refusing_a_question_that_had_an_answer_fails(self):
        case = {"id": "bq-06", "draft_expected": "answered"}
        score = score_case(case, _oracle([[502]]), "unsupported", None)
        assert not score.passed
        assert score.literal_match is False
        assert score.detail == "expected answered, got unsupported"


class TestExtraColumnsAreAllowed:
    """The module returns descriptive columns the answer key does not ask for
    (job id PLUS department and status). Demanding exact tuple equality marked
    correct answers wrong in the 2026-08-11 re-run."""

    def test_answer_key_values_may_be_a_subset_of_returned_columns(self):
        oracle = _oracle([[24693], [24694]], columns=("job_id",))
        actual = [
            {"job.id": 24693, "job.department_name": "STORE", "job.status": "IN PROGRESS"},
            {"job.id": 24694, "job.department_name": "STORE", "job.status": "IN PROGRESS"},
        ]
        assert values_match(oracle, actual)

    def test_a_wrong_value_still_fails_despite_extra_columns(self):
        oracle = _oracle([[24693], [24694]], columns=("job_id",))
        actual = [
            {"job.id": 24689, "job.department_name": "STORE"},
            {"job.id": 24694, "job.department_name": "STORE"},
        ]
        assert not values_match(oracle, actual)

    def test_row_count_must_still_match(self):
        oracle = _oracle([[24693], [24694]], columns=("job_id",))
        assert not values_match(oracle, [{"job.id": 24693, "x": 1}])

    def test_multi_column_key_matches_within_a_wider_row(self):
        oracle = _oracle([["STORE", 2]], columns=("department", "jobs"))
        assert values_match(oracle, [{"d": "store", "n": 2, "extra": "ignored"}])


class TestTotalCountScoring:
    """Some questions have a correct answer larger than any plan may return.

    "Which customers have never ordered" has 597 answers and the plan cap is 50.
    Demanding an exact row set is unmeetable; what IS measurable is whether the
    module found the right NUMBER and showed some of them.
    """

    def test_matching_total_row_count_passes_even_though_rows_are_capped(self):
        case = {"id": "bq-15", "draft_expected": "answered", "scoring": "total_count"}
        score = score_case(
            case,
            _oracle([[597]]),
            "answered",
            [{"customer.name": "1001 Organic Ltd"}, {"customer.name": "ZMMI"}],
            total_row_count=597,
        )
        assert score.passed

    def test_a_wrong_total_fails(self):
        case = {"id": "bq-15", "draft_expected": "answered", "scoring": "total_count"}
        score = score_case(
            case, _oracle([[597]]), "answered", [{"customer.name": "x"}], total_row_count=4830
        )
        assert not score.passed

    def test_the_right_total_with_no_rows_shown_fails(self):
        # "597 exist" while displaying nothing is not an answer to "which ones".
        case = {"id": "bq-15", "draft_expected": "answered", "scoring": "total_count"}
        score = score_case(case, _oracle([[597]]), "answered", [], total_row_count=597)
        assert not score.passed

    def test_default_scoring_is_still_exact(self):
        case = {"id": "bq-06", "draft_expected": "answered"}
        score = score_case(case, _oracle([[502]]), "answered", [{"n": 502}], total_row_count=1)
        assert score.passed


def _execution_event(*, result_rows=({"n": 502},)):
    from datetime import UTC, datetime, timedelta

    from app.business_query.seal.events import (
        BusinessQueryExecutionEvent,
        EventPayloadMode,
        ResultMember,
    )

    started = datetime(2026, 8, 13, tzinfo=UTC)
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
        result_members=(ResultMember(name="n", value_kind="integer"),),
        result_rows=result_rows,
        returned_row_count=len(result_rows),
        total_row_count=len(result_rows),
        truncated=False,
        database_identity="eval-db",
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        retention_at=started + timedelta(days=7),
        payload_mode=EventPayloadMode.ENCRYPTED,
    )


class TestScoreExecution:
    def test_matching_event_passes(self):
        from app.eval.business_query.scorer import score_execution

        spec = _spec(MemberMapping("v", "n", ValueKind.NON_MONEY))
        score = score_execution(
            {"id": "bq-06", "draft_expected": "answered"},
            spec,
            _oracle([[502]]),
            _execution_event(),
        )
        assert score.passed

    def test_wrong_event_values_fail(self):
        from app.eval.business_query.scorer import score_execution

        spec = _spec(MemberMapping("v", "n", ValueKind.NON_MONEY))
        score = score_execution(
            {"id": "bq-06", "draft_expected": "answered"},
            spec,
            _oracle([[502]]),
            _execution_event(result_rows=({"n": 4},)),
        )
        assert not score.passed
        assert score.literal_match is False
        assert score.detail == "values differ from answer key (1 expected row(s))"


class TestSchemaMismatchIsNamed:
    """A spec naming a member the result does not carry fails at the schema
    check, before any value is compared. Reported as "values differ" it reads
    as a wrong answer — which is how eval bq-10 hid a correct total behind a
    mis-named measure. The two must not share a detail string."""

    def test_member_named_by_spec_but_absent_from_result_says_so(self):
        case = {"id": "bq-10", "draft_expected": "answered"}
        score = score_case(
            case,
            _oracle([[5610750957.53]], columns=("still_owed",)),
            "answered",
            [{"overdue_outstanding": "5610750957.531499"}],
            total_row_count=1,
            truncated=False,
            actual_member_schema=("overdue_outstanding",),
            scoring_spec=_spec(MemberMapping("still_owed", "bill_outstanding", ValueKind.CURRENCY)),
        )

        assert score.literal_match is False
        assert not score.passed
        assert "bill_outstanding" in score.detail
        assert "overdue_outstanding" in score.detail
        assert "values differ from answer key" not in score.detail

    def test_a_real_value_difference_still_reads_as_a_value_difference(self):
        case = {"id": "bq-14", "draft_expected": "answered"}
        score = score_case(
            case,
            _oracle([[98171239.5]], columns=("cash_received",)),
            "answered",
            [{"cash_received": "96921239.5"}],
            total_row_count=1,
            truncated=False,
            actual_member_schema=("cash_received",),
            scoring_spec=_spec(MemberMapping("cash_received", "cash_received", ValueKind.CURRENCY)),
        )

        assert score.literal_match is False
        assert score.detail == "values differ from answer key (1 expected row(s))"

    def test_an_unallowlisted_extra_member_is_named_too(self):
        case = {"id": "bq-x", "draft_expected": "answered"}
        score = score_case(
            case,
            _oracle([[7]], columns=("n",)),
            "answered",
            [{"n": 7, "surprise.member": "TZS"}],
            total_row_count=1,
            truncated=False,
            actual_member_schema=("n", "surprise.member"),
            scoring_spec=_spec(MemberMapping("n", "n", ValueKind.NON_MONEY)),
        )

        assert score.literal_match is False
        assert "surprise.member" in score.detail


class TestAlternativeResultMembers:
    """bq-05 asks "what is the order number for job 24692". Both
    job.customer_order_number and customer_order.order_number return 743 and
    both answer the question; the module picks either run to run. A spec that
    names one grades the ROUTE, not the answer, and turns the case into a coin
    flip."""

    ORACLE = {"columns": ["order_number"], "rows": [["743"]], "row_count": 1}

    def _spec_with_alternatives(self):
        return _spec(
            MemberMapping(
                "order_number",
                "customer_order.order_number",
                ValueKind.NON_MONEY,
                result_member_alternatives=("job.customer_order_number",),
            )
        )

    @pytest.mark.parametrize("member", ["customer_order.order_number", "job.customer_order_number"])
    def test_either_declared_route_scores_the_same(self, member):
        score = score_case(
            {"id": "bq-05", "draft_expected": "answered"},
            self.ORACLE,
            "answered",
            [{member: "743"}],
            total_row_count=1,
            truncated=False,
            actual_member_schema=(member,),
            scoring_spec=self._spec_with_alternatives(),
        )
        assert score.literal_match is True
        assert score.passed

    def test_an_undeclared_member_is_still_a_schema_mismatch(self):
        score = score_case(
            {"id": "bq-05", "draft_expected": "answered"},
            self.ORACLE,
            "answered",
            [{"job.id": "743"}],
            total_row_count=1,
            truncated=False,
            actual_member_schema=("job.id",),
            scoring_spec=self._spec_with_alternatives(),
        )
        assert score.literal_match is False
        assert "job.id" in score.detail

    def test_a_wrong_value_on_an_alternative_route_still_fails(self):
        score = score_case(
            {"id": "bq-05", "draft_expected": "answered"},
            self.ORACLE,
            "answered",
            [{"job.customer_order_number": "740"}],
            total_row_count=1,
            truncated=False,
            actual_member_schema=("job.customer_order_number",),
            scoring_spec=self._spec_with_alternatives(),
        )
        assert score.literal_match is False
        assert score.detail == "values differ from answer key (1 expected row(s))"

    def test_a_route_cannot_be_shared_between_two_mappings(self):
        with pytest.raises(ValueError):
            _spec(
                MemberMapping("a", "shared.member", ValueKind.NON_MONEY),
                MemberMapping(
                    "b",
                    "other.member",
                    ValueKind.NON_MONEY,
                    result_member_alternatives=("shared.member",),
                ),
            )


class TestBothRoutesPresent:
    def test_a_result_carrying_two_declared_routes_says_which_ones(self):
        """Alternatives mean "either member may carry this column", so a result
        carrying BOTH leaves it undecided which one is the answer. That has to
        fail — but reported as "values differ" it accuses the number, which is
        the exact misdiagnosis alternatives were added to end."""
        score = score_case(
            {"id": "bq-05", "draft_expected": "answered"},
            {"columns": ["order_number"], "rows": [["743"]], "row_count": 1},
            "answered",
            [{"customer_order.order_number": "743", "job.customer_order_number": "743"}],
            total_row_count=1,
            truncated=False,
            actual_member_schema=("customer_order.order_number", "job.customer_order_number"),
            scoring_spec=_spec(
                MemberMapping(
                    "order_number",
                    "customer_order.order_number",
                    ValueKind.NON_MONEY,
                    result_member_alternatives=("job.customer_order_number",),
                )
            ),
        )

        assert score.literal_match is False
        assert not score.passed
        assert "more than one declared route" in score.detail
        assert "order_number" in score.detail
        assert "values differ from answer key" not in score.detail


def test_an_empty_result_schema_does_not_blame_the_answer_key():
    """A result with no members has nothing to compare, so naming the spec's
    members would accuse the gold of a mismatch the result never expressed."""
    score = score_case(
        {"id": "bq-x", "draft_expected": "answered"},
        {"columns": ["n"], "rows": [[7]], "row_count": 1},
        "answered",
        [],
        total_row_count=1,
        truncated=False,
        actual_member_schema=(),
        scoring_spec=_spec(MemberMapping("n", "n", ValueKind.NON_MONEY)),
    )
    assert score.literal_match is False
    assert "spec expects" not in score.detail
