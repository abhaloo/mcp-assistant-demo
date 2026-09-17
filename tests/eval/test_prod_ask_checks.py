"""Every production-ask check must fail on the defect it exists to catch.

A scorer that cannot fail is worse than no scorer, so each test below builds
the smallest answer that breaks one property and asserts that exactly that
check turns red.
"""

from __future__ import annotations

import pytest

from app.eval.ask_route.case import (
    AskObservation,
    ProdAskCase,
    RunMode,
    SqlOracleRow,
    Transport,
)
from app.eval.ask_route.checks import (
    CheckResult,
    SkippedCheck,
    check_accuracy,
    check_citations,
    check_follow_ups,
    check_permission_variance,
    check_rich_text,
    corpus_tier_of,
)
from app.models.schemas import Answer, CitationsPayload, CitedSourceRef, Source

MANIFEST = frozenset({"invoice", "job", "customer", "inventory", "supplier"})

PRICING = "data/corpus/company/sales/customer-pricing-guide.md"
HANDBOOK = "data/corpus/company/all/company-handbook.md"


def make_case(**overrides) -> ProdAskCase:
    base = {
        "id": "t-01",
        "dimensions": ["citations"],
        "question": "What does a job cost?",
        "principal": {"label": "p", "role": "sales", "permissions": ["view invoice"]},
        "route": "semantic",
        "expected_access_tiers": ["all", "sales"],
        "modes": ["live"],
        "grounding": "fixture",
        "citations": {
            "expect_parsed": True,
            "supporting_source_files": [],
            "min_cited": 0,
            "max_cited": None,
        },
    }
    base.update(overrides)
    return ProdAskCase.model_validate(base)


def make_answer(
    text: str,
    *,
    sources: list[Source] | None = None,
    citations: CitationsPayload | None = None,
    chips: list[str] | None = None,
) -> Answer:
    return Answer(
        question="q",
        answer=text,
        sources=sources or [],
        model="fixture",
        citations=citations or CitationsPayload(parsed=False),
        follow_up_suggestions=chips or [],
    )


def observe(
    answer: Answer,
    *,
    mode: RunMode = "live",
    transport: Transport = "json",
    retrieved: list[str] | None = None,
    tiers: list[str] | None = None,
) -> AskObservation:
    return AskObservation(
        case_id="t-01",
        mode=mode,
        transport=transport,
        answer=answer,
        retrieved_source_ids=retrieved,
        granted_access_tiers=tiers,
    )


def result_for(outcomes, check_id: str) -> CheckResult:
    matches = [o for o in outcomes if o.check_id == check_id and isinstance(o, CheckResult)]
    assert matches, f"{check_id} did not run; got {[o.check_id for o in outcomes]}"
    return matches[0]


def skip_for(outcomes, check_id: str) -> SkippedCheck:
    matches = [o for o in outcomes if o.check_id == check_id and isinstance(o, SkippedCheck)]
    assert matches, f"{check_id} was not skipped"
    return matches[0]


def source(path: str, chunk: int, marker: int | None) -> Source:
    return Source(
        id=f"{path}:{chunk}",
        content="",
        source_file=path,
        chunk_index=chunk,
        marker=marker,
    )


# ---------------------------------------------------------------------------
# Accuracy
# ---------------------------------------------------------------------------


ACCURACY_CASE = {
    "dimensions": ["accuracy"],
    "accuracy": {
        "oracle": {"kind": "corpus", "corpus_file": PRICING, "locator": "Business Cards"},
        "required_facts": [
            {"label": "single", "kind": "number", "number": 140},
            {"label": "premium", "kind": "number", "number": 260},
        ],
        "forbidden_facts": [{"label": "salary", "kind": "text", "text": "AED 6,000"}],
    },
    "citations": None,
}


def test_required_fact_missing_fails():
    case = make_case(**ACCURACY_CASE)
    outcomes = check_accuracy(case, observe(make_answer("Cards cost Tsh 140.")), None)
    check = result_for(outcomes, "accuracy.required_facts_present")
    assert not check.passed
    assert "premium" in check.detail


def test_required_facts_present_passes_with_thousands_separator():
    case = make_case(
        **{
            **ACCURACY_CASE,
            "accuracy": {
                **ACCURACY_CASE["accuracy"],
                "required_facts": [{"label": "big", "kind": "number", "number": 12400}],
            },
        }
    )
    outcomes = check_accuracy(case, observe(make_answer("Balance is Tsh 12,400 today.")), None)
    assert result_for(outcomes, "accuracy.required_facts_present").passed


def test_forbidden_fact_leak_fails():
    case = make_case(**ACCURACY_CASE)
    answer = make_answer("Cards cost Tsh 140 and Tsh 260. Managers earn AED 6,000.")
    check = result_for(
        check_accuracy(case, observe(answer), None), "accuracy.forbidden_facts_absent"
    )
    assert not check.passed
    assert "salary" in check.detail


def test_forbidden_fact_check_is_skipped_in_stub_mode():
    case = make_case(**ACCURACY_CASE)
    outcomes = check_accuracy(case, observe(make_answer("nothing"), mode="stub"), None)
    assert "scripts the answer text" in skip_for(outcomes, "accuracy.forbidden_facts_absent").reason


def test_out_of_tier_retrieval_fails():
    case = make_case(**ACCURACY_CASE)
    observation = observe(
        make_answer("Tsh 140 and Tsh 260."),
        retrieved=[f"{HANDBOOK}:0", "data/corpus/company/admin/salary-records.md:0"],
    )
    check = result_for(
        check_accuracy(case, observation, None), "accuracy.no_out_of_tier_source_retrieved"
    )
    assert not check.passed
    assert "admin" in check.detail


def test_in_tier_retrieval_passes():
    case = make_case(**ACCURACY_CASE)
    observation = observe(make_answer("Tsh 140 and Tsh 260."), retrieved=[f"{PRICING}:0"])
    assert result_for(
        check_accuracy(case, observation, None), "accuracy.no_out_of_tier_source_retrieved"
    ).passed


def test_widened_access_tiers_fail():
    case = make_case(**ACCURACY_CASE)
    observation = observe(make_answer("Tsh 140, Tsh 260."), tiers=["all", "sales", "finance"])
    check = result_for(
        check_accuracy(case, observation, None), "accuracy.granted_tiers_match_policy"
    )
    assert not check.passed
    assert "finance" in check.detail


def test_sql_oracle_value_missing_fails():
    case = make_case(
        dimensions=["accuracy"],
        citations=None,
        accuracy={
            "oracle": {"kind": "sql", "sql_case_id": "pa-13"},
            "required_facts": [],
            "forbidden_facts": [],
        },
    )
    oracle = SqlOracleRow(
        case_id="pa-13",
        sql_sha256="0" * 64,
        columns=["month", "invoices_count"],
        row_count=2,
        rows=[["2026-01", 93], ["2026-02", 61]],
    )
    outcomes = check_accuracy(case, observe(make_answer("January had 93 invoices.")), oracle)
    check = result_for(outcomes, "accuracy.sql_oracle_values_present")
    assert not check.passed
    assert "invoices_count=61" in check.detail


def test_sql_oracle_all_values_present_passes():
    case = make_case(
        dimensions=["accuracy"],
        citations=None,
        accuracy={
            "oracle": {"kind": "sql", "sql_case_id": "pa-13"},
            "required_facts": [],
            "forbidden_facts": [],
        },
    )
    oracle = SqlOracleRow(
        case_id="pa-13",
        sql_sha256="0" * 64,
        columns=["month", "invoices_count"],
        row_count=2,
        rows=[["2026-01", 93], ["2026-02", 61]],
    )
    answer = make_answer("2026-01: 93 invoices. 2026-02: 61 invoices.")
    assert result_for(
        check_accuracy(case, observe(answer), oracle), "accuracy.sql_oracle_values_present"
    ).passed


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        (f"{PRICING}:0", "sales"),
        ("data/corpus/company/graphic-design/file-handoff-standards.md:2", "graphic design"),
        ("some/other/path.md:1", None),
    ],
)
def test_corpus_tier_of(source_id, expected):
    assert corpus_tier_of(source_id) == expected


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def test_parsed_flag_mismatch_fails():
    case = make_case()
    answer = make_answer("no markers here", citations=CitationsPayload(parsed=False))
    assert not result_for(
        check_citations(case, observe(answer)), "citations.parsed_matches_expectation"
    ).passed


def test_citation_count_below_minimum_fails():
    case = make_case(
        citations={
            "expect_parsed": True,
            "supporting_source_files": [],
            "min_cited": 1,
            "max_cited": None,
        }
    )
    answer = make_answer("text", citations=CitationsPayload(parsed=True))
    assert not result_for(
        check_citations(case, observe(answer)), "citations.count_within_bounds"
    ).passed


def test_citation_count_above_maximum_fails():
    case = make_case(
        citations={
            "expect_parsed": True,
            "supporting_source_files": [],
            "min_cited": 0,
            "max_cited": 1,
        }
    )
    answer = make_answer(
        "a [Source 1] b [Source 2]",
        citations=CitationsPayload(
            parsed=True,
            cited=[
                CitedSourceRef(marker=1, id=f"{PRICING}:0"),
                CitedSourceRef(marker=2, id=f"{HANDBOOK}:0"),
            ],
        ),
    )
    assert not result_for(
        check_citations(case, observe(answer)), "citations.count_within_bounds"
    ).passed


def test_payload_marker_absent_from_body_fails():
    case = make_case()
    answer = make_answer(
        "Only one marker here [Source 1].",
        citations=CitationsPayload(
            parsed=True,
            cited=[
                CitedSourceRef(marker=1, id=f"{PRICING}:0"),
                CitedSourceRef(marker=3, id=f"{HANDBOOK}:0"),
            ],
        ),
    )
    check = result_for(
        check_citations(case, observe(answer)), "citations.payload_markers_appear_in_body"
    )
    assert not check.passed
    assert "[3]" in check.detail


def test_unbound_marker_in_body_fails():
    case = make_case()
    answer = make_answer(
        "First [Source 1]. Second [Source 2].",
        citations=CitationsPayload(
            parsed=True, cited=[CitedSourceRef(marker=1, id=f"{PRICING}:0")]
        ),
    )
    check = result_for(
        check_citations(case, observe(answer)), "citations.no_unbound_markers_in_body"
    )
    assert not check.passed
    assert "[2]" in check.detail


def test_marker_text_with_parsed_false_fails():
    case = make_case(
        citations={
            "expect_parsed": False,
            "supporting_source_files": [],
            "min_cited": 0,
            "max_cited": 0,
        }
    )
    answer = make_answer("Stray [Source 4] marker.", citations=CitationsPayload(parsed=False))
    assert not result_for(
        check_citations(case, observe(answer)), "citations.no_unbound_markers_in_body"
    ).passed


def test_citing_a_source_that_was_never_retrieved_fails():
    case = make_case()
    answer = make_answer(
        "Claim [Source 1].",
        sources=[source(PRICING, 0, 1)],
        citations=CitationsPayload(
            parsed=True, cited=[CitedSourceRef(marker=1, id=f"{PRICING}:0")]
        ),
    )
    observation = observe(answer, retrieved=[f"{HANDBOOK}:0"])
    check = result_for(check_citations(case, observation), "citations.cited_ids_were_retrieved")
    assert not check.passed
    assert not check.degraded


def test_cited_ids_check_degrades_without_a_retrieval_set():
    case = make_case()
    answer = make_answer(
        "Claim [Source 1].",
        sources=[source(PRICING, 0, 1)],
        citations=CitationsPayload(
            parsed=True, cited=[CitedSourceRef(marker=1, id=f"{PRICING}:0")]
        ),
    )
    check = result_for(check_citations(case, observe(answer)), "citations.cited_ids_were_retrieved")
    assert check.passed
    assert check.degraded
    assert "no pre-filter retrieval set recorded" in check.detail


def test_citing_the_wrong_document_fails_the_allowlist():
    case = make_case(
        citations={
            "expect_parsed": True,
            "supporting_source_files": ["customer-pricing-guide.md"],
            "min_cited": 1,
            "max_cited": None,
        }
    )
    answer = make_answer(
        "Gold Corporate customers get 15% [Source 1].",
        sources=[source(HANDBOOK, 0, 1)],
        citations=CitationsPayload(
            parsed=True, cited=[CitedSourceRef(marker=1, id=f"{HANDBOOK}:0")]
        ),
    )
    check = result_for(
        check_citations(case, observe(answer)), "citations.cited_files_contain_the_claim"
    )
    assert not check.passed
    assert "company-handbook.md" in check.detail


# ---------------------------------------------------------------------------
# Rich text
# ---------------------------------------------------------------------------


def rich_case(structure: dict, **rich) -> ProdAskCase:
    return make_case(
        dimensions=["rich_text"],
        citations=None,
        rich_text={
            "structure": structure,
            "currency_tokens": rich.get("currency_tokens", []),
            "forbidden_currency_symbols": rich.get("forbidden_currency_symbols", ["$"]),
            "oracle_amounts": rich.get("oracle_amounts", []),
        },
    )


TABLE_3 = {"kind": "table", "min_body_rows": 3, "min_columns": 2, "min_items": 0}
PROSE = {"kind": "prose", "min_body_rows": 0, "min_columns": 0, "min_items": 0}

GOOD_TABLE = (
    "| Finish | Price |\n| --- | --- |\n| Single | Tsh 140 |\n"
    "| Double | Tsh 170 |\n| Premium | Tsh 260 |\n"
)


def test_run_on_sentence_fails_a_table_case():
    case = rich_case(TABLE_3)
    answer = make_answer("Single is Tsh 140, double is Tsh 170 and premium is Tsh 260.")
    check = result_for(
        check_rich_text(case, observe(answer)), "rich_text.structure_matches_result_shape"
    )
    assert not check.passed
    assert "no table" in check.detail


def test_short_table_fails_the_row_floor():
    case = rich_case(TABLE_3)
    answer = make_answer("| Finish | Price |\n| --- | --- |\n| Single | Tsh 140 |\n")
    check = result_for(
        check_rich_text(case, observe(answer)), "rich_text.structure_matches_result_shape"
    )
    assert not check.passed
    assert "1 body rows" in check.detail


def test_narrow_table_fails_the_column_floor():
    case = rich_case({"kind": "table", "min_body_rows": 2, "min_columns": 3, "min_items": 0})
    answer = make_answer("| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n")
    check = result_for(
        check_rich_text(case, observe(answer)), "rich_text.structure_matches_result_shape"
    )
    assert not check.passed
    assert "2 columns" in check.detail


def test_good_table_passes():
    case = rich_case(TABLE_3)
    outcomes = check_rich_text(case, observe(make_answer(GOOD_TABLE)))
    assert result_for(outcomes, "rich_text.structure_matches_result_shape").passed
    assert result_for(outcomes, "rich_text.markdown_well_formed").passed


def test_too_few_list_items_fails():
    case = rich_case({"kind": "list", "min_body_rows": 0, "min_columns": 0, "min_items": 3})
    answer = make_answer("- one\n- two\n")
    assert not result_for(
        check_rich_text(case, observe(answer)), "rich_text.structure_matches_result_shape"
    ).passed


def test_structure_check_is_skipped_in_stub_mode():
    case = rich_case(TABLE_3)
    outcomes = check_rich_text(case, observe(make_answer("prose"), mode="stub"))
    assert skip_for(outcomes, "rich_text.structure_matches_result_shape")


@pytest.mark.parametrize(
    "body",
    [
        "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n",
        "| A | B |\n| --- | --- |\n",
        "```python\nprint(1)\n",
        "Here is <b>bold</b> HTML.",
        "See https://example.com for details.",
        "This is **unclosed bold.",
        "| A | B |\n| 1 | 2 |\n",
    ],
    ids=[
        "ragged-row",
        "header-without-body",
        "unclosed-fence",
        "raw-html",
        "url-in-body",
        "unclosed-bold",
        "pipe-rows-without-delimiter",
    ],
)
def test_malformed_markdown_fails(body):
    case = rich_case(PROSE)
    assert not result_for(
        check_rich_text(case, observe(make_answer(body))), "rich_text.markdown_well_formed"
    ).passed


def test_amount_without_a_currency_label_fails():
    case = rich_case(PROSE, currency_tokens=["Tsh"], oracle_amounts=[12400.0])
    answer = make_answer("The balance is 12,400 and it is due on Friday.")
    check = result_for(check_rich_text(case, observe(answer)), "rich_text.currency_rendering")
    assert not check.passed
    assert "12400" in check.detail


def test_amount_with_a_currency_label_passes():
    case = rich_case(PROSE, currency_tokens=["Tsh"], oracle_amounts=[12400.0])
    answer = make_answer("The balance is Tsh 12,400.")
    assert result_for(check_rich_text(case, observe(answer)), "rich_text.currency_rendering").passed


def test_foreign_currency_symbol_fails():
    case = rich_case(PROSE, currency_tokens=["Tsh"], oracle_amounts=[12400.0])
    answer = make_answer("The balance is $12,400.")
    check = result_for(check_rich_text(case, observe(answer)), "rich_text.currency_rendering")
    assert not check.passed
    assert "foreign currency symbol" in check.detail


def test_missing_amount_is_not_reported_as_a_formatting_defect():
    """Absence of the number is an accuracy failure, not a currency failure."""
    case = rich_case(PROSE, currency_tokens=["Tsh"], oracle_amounts=[12400.0])
    assert result_for(
        check_rich_text(case, observe(make_answer("I do not have that figure."))),
        "rich_text.currency_rendering",
    ).passed


# ---------------------------------------------------------------------------
# Follow-up suggestions
# ---------------------------------------------------------------------------


def follow_up_case(**spec) -> ProdAskCase:
    base = {
        "expect_any": True,
        "question_resources": ["job"],
        "max_suggestions": 3,
        "expected_varies_by_permission": False,
    }
    base.update(spec)
    return make_case(
        dimensions=["follow_ups"],
        citations=None,
        question="How many jobs are open?",
        follow_ups=base,
    )


def unvalidated_answer(chips: list[str]) -> Answer:
    """An Answer that skips validation.

    The response schema already caps suggestions at three, dedupes them, and
    normalizes whitespace, so a live route cannot produce these shapes. The
    bounded check exists for replay of records written by an older or foreign
    serializer, and it is tested on exactly that input.
    """
    return Answer.model_construct(
        question="q",
        answer="a",
        sources=[],
        model="fixture",
        citations=CitationsPayload(parsed=False),
        follow_up_suggestions=chips,
    )


def test_too_many_chips_fail():
    case = follow_up_case()
    chips = [
        "Show the most recent jobs",
        "Show jobs by status",
        "Show jobs by priority",
        "More jobs",
    ]
    check = result_for(
        check_follow_ups(case, observe(unvalidated_answer(chips)), MANIFEST),
        "follow_ups.bounded",
    )
    assert not check.passed


def test_duplicate_chips_fail():
    case = follow_up_case()
    chips = ["Show the most recent jobs", "show the most recent JOBS"]
    assert not result_for(
        check_follow_ups(case, observe(unvalidated_answer(chips)), MANIFEST),
        "follow_ups.bounded",
    ).passed


def test_unnormalized_whitespace_in_a_chip_fails():
    case = follow_up_case()
    chips = ["Show  the most recent jobs"]
    assert not result_for(
        check_follow_ups(case, observe(unvalidated_answer(chips)), MANIFEST),
        "follow_ups.bounded",
    ).passed


def test_unexpected_chips_fail_presence():
    case = follow_up_case(expect_any=False, question_resources=[])
    assert not result_for(
        check_follow_ups(
            case, observe(make_answer("a", chips=["Show the most recent jobs"])), MANIFEST
        ),
        "follow_ups.presence_matches_expectation",
    ).passed


def test_chip_naming_an_unasked_resource_fails():
    case = follow_up_case()
    chips = ["Show the most recent invoices"]
    check = result_for(
        check_follow_ups(case, observe(make_answer("a", chips=chips)), MANIFEST),
        "follow_ups.no_new_resource_disclosed",
    )
    assert not check.passed
    assert "invoice" in check.detail


def test_generic_chip_fails_specificity():
    case = follow_up_case()
    chips = ["Tell me more"]
    assert not result_for(
        check_follow_ups(case, observe(make_answer("a", chips=chips)), MANIFEST),
        "follow_ups.specific_to_the_question",
    ).passed


def test_chip_naming_no_declared_resource_fails():
    case = follow_up_case()
    chips = ["Show the most recent widgets"]
    assert not result_for(
        check_follow_ups(case, observe(make_answer("a", chips=chips)), MANIFEST),
        "follow_ups.resources_declared_by_manifest",
    ).passed


def test_plural_resource_label_is_recognized():
    """The producer pluralizes 'inventory' to 'inventories'; the check must follow."""
    case = follow_up_case(question_resources=["inventory"])
    case = make_case(
        dimensions=["follow_ups"],
        citations=None,
        question="What do I do with a damaged inventory item?",
        follow_ups={
            "expect_any": True,
            "question_resources": ["inventory"],
            "max_suggestions": 3,
            "expected_varies_by_permission": False,
        },
    )
    outcomes = check_follow_ups(
        case, observe(make_answer("a", chips=["Show the most recent inventories"])), MANIFEST
    )
    assert result_for(outcomes, "follow_ups.specific_to_the_question").passed
    assert result_for(outcomes, "follow_ups.resources_declared_by_manifest").passed


def test_permission_variance_flags_an_unexpected_difference():
    case = follow_up_case(expected_varies_by_permission=False)
    mine = observe(make_answer("a", chips=["Show the most recent jobs"]))
    theirs = observe(make_answer("a", chips=[]))
    check = check_permission_variance(case, mine, theirs)
    assert isinstance(check, CheckResult)
    assert not check.passed


def test_permission_variance_flags_a_missing_difference():
    case = follow_up_case(expected_varies_by_permission=True)
    chips = ["Show the most recent jobs"]
    mine = observe(make_answer("a", chips=chips))
    theirs = observe(make_answer("a", chips=list(chips)))
    check = check_permission_variance(case, mine, theirs)
    assert isinstance(check, CheckResult)
    assert not check.passed
