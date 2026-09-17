"""Unit tests for the conversational coordinator evaluation scorer and models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.conversation.coordinator.contracts import (
    AnswerBlock,
    CoordinatorContext,
    FinishAnswer,
)
from app.conversation.coordinator.runtime import FinishedDraft


def test_models_importable():
    from app.eval.conversation_coordinator import (
        CaseResult,
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    assert CoordinatorCase is not None
    assert CaseResult is not None
    assert CoordinatorRunOutput is not None
    assert callable(score)


def test_coordinator_case_schema():
    from app.eval.conversation_coordinator import CoordinatorCase

    ctx = CoordinatorContext(turn_id="t1", question="What was July revenue?")
    case = CoordinatorCase(
        case_id="co-01",
        stratum="fresh_bq",
        context=ctx,
        expected_sources=("bq-july",),
        required_actions=("query_business", "finish_answer"),
        allowed_alternatives=(("clarify",),),
        forbidden_calls=("search_documents",),
        expected_question_origin="user",
        answer_oracle="TZS 302,778,395",
        provenance="Author: Test author",
    )
    assert case.case_id == "co-01"
    assert case.stratum == "fresh_bq"
    assert case.expected_sources == ("bq-july",)

    # Extra field forbidden
    with pytest.raises(ValidationError):
        CoordinatorCase(
            case_id="co-02",
            stratum="fresh_bq",
            context=ctx,
            required_actions=("finish_answer",),
            provenance="test",
            unknown_field="extra",
        )


def test_case_result_schema():
    from app.eval.conversation_coordinator import CaseResult

    res = CaseResult(
        case_id="co-01",
        trajectory="pass",
        sources="pass",
        grounding="pass",
        unnecessary_calls=0,
        time_to_first_token_ms=120,
        latency_ms=450,
        tokens=350,
    )
    assert res.case_id == "co-01"
    assert res.trajectory == "pass"
    assert res.sources == "pass"
    assert res.grounding == "pass"

    with pytest.raises(ValidationError):
        CaseResult(
            case_id="co-01",
            trajectory="unknown_axis",
            sources="pass",
            grounding="pass",
            unnecessary_calls=0,
            time_to_first_token_ms=None,
            latency_ms=None,
            tokens=None,
        )


def test_score_missing_actions_fails_trajectory():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t1", question="Show revenue")
    case = CoordinatorCase(
        case_id="c-missing",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("finish_answer",),
        answer_text="Here is your answer without running the query.",
    )
    result = score(case, run)
    assert result.trajectory == "fail"


def test_score_extra_bq_fails_trajectory_and_counts_unnecessary():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t2", question="Hello!")
    case = CoordinatorCase(
        case_id="c-extra",
        stratum="general",
        context=ctx,
        required_actions=("finish_answer",),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        answer_text="Hello! I also queried the database for no reason.",
        unnecessary_calls=1,
    )
    result = score(case, run)
    assert result.trajectory == "fail"
    assert result.unnecessary_calls >= 1


def test_score_wrong_source_fails_sources():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t3", question="Explain July revenue")
    case = CoordinatorCase(
        case_id="c-wrong-src",
        stratum="explanation",
        context=ctx,
        expected_sources=("source-july",),
        required_actions=("explain_sources", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("explain_sources", "finish_answer"),
        sources=("source-august",),
        answer_text="Here is July revenue explanation.",
    )
    result = score(case, run)
    assert result.sources == "fail"


def test_score_forbidden_query_fails_trajectory_and_grounding():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t4", question="Explain saved July number")
    case = CoordinatorCase(
        case_id="c-forbidden",
        stratum="explanation",
        context=ctx,
        required_actions=("explain_sources", "finish_answer"),
        forbidden_calls=("query_business",),
        answer_oracle="80%",
        provenance="test",
    )
    # The final value is reached, but via forbidden query_business
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        answer_text="The collection rate is 80%.",
    )
    result = score(case, run)
    assert result.trajectory == "fail"
    assert result.grounding == "fail"


def test_score_false_tool_receipts_fails():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t5", question="What was revenue?")
    case = CoordinatorCase(
        case_id="c-false-receipts",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        answer_text="Some number",
        false_tool_receipts=True,
    )
    result = score(case, run)
    assert result.trajectory == "fail"


def test_score_missing_model_usage_is_unproven():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t6", question="Show revenue")
    case = CoordinatorCase(
        case_id="c-missing-usage",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        answer_text="Here is the revenue",
        tokens=None,
        missing_model_usage=True,
    )
    result = score(case, run)
    assert result.trajectory == "unproven"
    assert result.sources == "unproven"
    assert result.grounding == "unproven"


def test_score_unknown_dict_without_capture_flag_is_unproven():
    """Incomplete foreign dicts fail closed. Validation spec: missing capture is UNPROVEN."""
    from app.eval.conversation_coordinator import CoordinatorCase, score

    ctx = CoordinatorContext(turn_id="t-dict", question="Show revenue")
    case = CoordinatorCase(
        case_id="c-dict-unproven",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        provenance="test",
    )
    result = score(
        case,
        {
            "actions": ("query_business", "finish_answer"),
            "answer_text": "Revenue was 100",
        },
    )
    assert result.trajectory == "unproven"
    assert result.sources == "unproven"
    assert result.grounding == "unproven"


def test_score_incomplete_capture_is_unproven():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t7", question="Show revenue")
    case = CoordinatorCase(
        case_id="c-incomplete",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        answer_text="Revenue was 100",
        capture_complete=False,
    )
    result = score(case, run)
    assert result.trajectory == "unproven"
    assert result.sources == "unproven"
    assert result.grounding == "unproven"


def test_score_wrong_question_origin_fails_trajectory():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t8", question="What was revenue?")
    case = CoordinatorCase(
        case_id="c-origin",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        expected_question_origin="user",
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        question_origin="model",
        answer_text="Revenue is 100",
    )
    result = score(case, run)
    assert result.trajectory == "fail"


def test_score_allowed_alternative_passes():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t9", question="Ambiguous question")
    case = CoordinatorCase(
        case_id="c-alt",
        stratum="ambiguity_regeneration_focus",
        context=ctx,
        required_actions=("search_documents", "finish_answer"),
        allowed_alternatives=(("clarify",),),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("clarify",),
        answer_text="Which document did you mean?",
    )
    result = score(case, run)
    assert result.trajectory == "pass"
    assert result.grounding == "unproven"


def test_score_required_sequence_not_penalized_for_cheaper_alternative():
    """Matching required actions must not count as unnecessary vs a shorter alternative."""
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t-amb", question="Regenerate that answer.")
    case = CoordinatorCase(
        case_id="c-amb-required",
        stratum="ambiguity_regeneration_focus",
        context=ctx,
        required_actions=("explain_sources", "finish_answer"),
        allowed_alternatives=(("finish_answer",),),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("explain_sources", "finish_answer"),
        answer_text="Focusing on the original July result.",
        tokens=40,
    )
    result = score(case, run)
    assert result.trajectory == "pass"
    assert result.unnecessary_calls == 0


def test_score_absent_oracle_is_unproven_grounding():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t-none", question="Hello")
    case = CoordinatorCase(
        case_id="c-no-oracle",
        stratum="general",
        context=ctx,
        required_actions=("finish_answer",),
        answer_oracle=None,
        provenance="test",
    )
    run = CoordinatorRunOutput(actions=("finish_answer",), answer_text="Hello.", tokens=12)
    result = score(case, run)
    assert result.trajectory == "pass"
    assert result.grounding == "unproven"


def test_score_oracle_does_not_match_inside_larger_number():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t-pct", question="August collection rate?")
    case = CoordinatorCase(
        case_id="c-pct",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        answer_oracle="60%",
        provenance="docs/superpowers/specs/2026-09-15-coordinator-intent-evidence-fixtures.json",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        answer_text="Growth reached 160% this year.",
        tokens=20,
    )
    result = score(case, run)
    assert result.grounding == "fail"


def test_score_full_pass():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t10", question="July revenue?")
    case = CoordinatorCase(
        case_id="c-pass",
        stratum="fresh_bq",
        context=ctx,
        expected_sources=("bq-july",),
        required_actions=("query_business", "finish_answer"),
        expected_question_origin="user",
        answer_oracle="302,778,395",
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        sources=("bq-july",),
        question_origin="user",
        answer_text="The July invoiced revenue is TZS 302,778,395.18.",
        time_to_first_token_ms=150,
        latency_ms=620,
        tokens=420,
    )
    result = score(case, run)
    assert result.trajectory == "pass"
    assert result.sources == "pass"
    assert result.grounding == "pass"
    assert result.unnecessary_calls == 0
    assert result.latency_ms == 620
    assert result.tokens == 420


def test_score_with_coordinator_terminal_finished_draft():
    from app.eval.conversation_coordinator import CoordinatorCase, score

    ctx = CoordinatorContext(turn_id="t11", question="Say hello")
    case = CoordinatorCase(
        case_id="c-terminal",
        stratum="general",
        context=ctx,
        required_actions=("finish_answer",),
        provenance="test",
    )
    draft = FinishAnswer(
        blocks=(
            AnswerBlock(
                text="Hello! How can I help you today?",
                claim_type="general",
            ),
        )
    )
    terminal = FinishedDraft(
        draft=draft,
        observations=(),
        outcomes=(),
        answer_mode="direct",
    )
    result = score(case, terminal)
    assert result.trajectory == "unproven"
    assert result.sources == "unproven"
    assert result.grounding == "unproven"


def test_score_document_case_without_frozen_sources_is_unproven():
    """No frozen source identities means the axis cannot be judged; a vacuous
    fail would teach readers to ignore the column (ADR 0025)."""
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    case = CoordinatorCase(
        case_id="c-doc-unfrozen",
        stratum="documents_mixed",
        context=CoordinatorContext(turn_id="t9", question="What does the policy say?"),
        required_actions=("search_documents", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("search_documents", "finish_answer"),
        sources=("doc-1",),
        answer_text="The policy says contact after 30 days.",
    )
    assert score(case, run).sources == "unproven"


def test_score_business_only_case_with_no_sources_still_passes_sources():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    case = CoordinatorCase(
        case_id="c-bq-nosrc",
        stratum="fresh_bq",
        context=CoordinatorContext(turn_id="t10", question="How many jobs?"),
        required_actions=("query_business", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        sources=(),
        answer_text="7",
    )
    assert score(case, run).sources == "pass"


def test_coordinator_run_output_time_to_first_action_ms():
    from app.eval.conversation_coordinator import CoordinatorRunOutput, _extract_run_data

    # Default is None
    out_default = CoordinatorRunOutput()
    assert out_default.time_to_first_action_ms is None

    # Explicit value
    out_explicit = CoordinatorRunOutput(time_to_first_action_ms=145)
    assert out_explicit.time_to_first_action_ms == 145

    # Extracted from dict
    dict_input = {
        "actions": ("finish_answer",),
        "answer_text": "hello",
        "time_to_first_action_ms": 210,
        "capture_complete": True,
    }
    extracted_dict = _extract_run_data(dict_input)
    assert extracted_dict.time_to_first_action_ms == 210

    # Extracted from generic object
    class GenericOutput:
        actions = ("finish_answer",)
        answer_text = "hello"
        time_to_first_action_ms = 320
        capture_complete = True

    extracted_obj = _extract_run_data(GenericOutput())
    assert extracted_obj.time_to_first_action_ms == 320


def test_trajectory_invariants_name_the_policy_a_run_broke():
    """Case-independent rules every accepted trajectory obeys, from
    CoordinatorPolicy and the section 13 ledger sequence (query_business,
    then explain_sources on the answer already in hand)."""
    from app.eval.conversation_coordinator import trajectory_invariants

    assert trajectory_invariants(("query_business", "finish_answer")) == ()
    assert trajectory_invariants(("explain_sources", "finish_answer")) == ()
    assert trajectory_invariants(("search_documents", "search_documents", "finish_answer")) == ()
    assert trajectory_invariants(("query_business", "explain_sources", "finish_answer")) == (
        "explain_after_action",
    )
    assert trajectory_invariants(("query_business", "query_business", "finish_answer")) == (
        "business_query_repeated",
    )
    assert trajectory_invariants(
        ("search_documents", "search_documents", "search_documents", "finish_answer")
    ) == ("document_search_repeated",)
    assert trajectory_invariants(("explain_sources", "query_business", "finish_answer")) == (
        "tool_after_explanation",
    )


def test_score_fails_trajectory_on_an_invariant_even_when_the_case_allows_the_sequence():
    """An authored alternative can never license a policy break: the
    invariant is checked before the case's own sequences."""
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t13", question="who were our top customers this year")
    case = CoordinatorCase(
        case_id="c-invariant",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        allowed_alternatives=(("query_business", "explain_sources", "finish_answer"),),
        provenance="test",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "explain_sources", "finish_answer"),
        answer_text="…",
    )

    result = score(case, run)

    assert result.trajectory == "fail"
    assert result.invariant_violations == ("explain_after_action",)


def test_score_reports_no_violation_on_a_clean_run():
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    ctx = CoordinatorContext(turn_id="t14", question="July revenue?")
    case = CoordinatorCase(
        case_id="c-clean",
        stratum="fresh_bq",
        context=ctx,
        required_actions=("query_business", "finish_answer"),
        provenance="test",
    )
    run = CoordinatorRunOutput(actions=("query_business", "finish_answer"), answer_text="x")

    assert score(case, run).invariant_violations == ()
