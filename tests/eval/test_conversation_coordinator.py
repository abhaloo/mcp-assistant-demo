from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.conversation.coordinator.contracts import (
    AnswerBlock,
    CoordinatorContext,
    FinishAnswer,
)
from app.conversation.coordinator.runtime import FinishedDraft
from app.eval.ask_route.text import normalize_text
from app.eval.conversation_coordinator import load_coordinator_cases
from app.paths import REPO_ROOT
from app.rag.access_tiers import get_access_tiers
from scripts.eval.eval_tokens import PROFILES


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


def test_history_line_accepts_optional_plan_digest() -> None:
    """Oracle: spec §4.2 typed dialogue — HistoryLine carries a PlanDigest for follow-up cases."""
    from app.business_query.plan.plan_diff import PlanDigest
    from app.conversation.coordinator.contracts import HistoryLine

    digest = PlanDigest(
        grain="entity_rows",
        anchor="receipt",
        dimensions=("receipt.id",),
        measures=(),
        limit=10,
        period=None,
        set_ids=("latest_receipts",),
        plan_fingerprint="0" * 64,
    )
    line = HistoryLine(exchange_id="ex-0", user_text="list receipts", digest=digest)
    assert line.digest is not None
    assert line.digest.anchor == "receipt"
    assert HistoryLine(exchange_id="ex-1", user_text="hello").digest is None


def test_score_requires_patch_tier_when_origin_is_model() -> None:
    """Oracle: PREFLIGHT 28 — expected origin model requires continuation_tier patch."""
    from app.conversation.coordinator.contracts import CoordinatorContext
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    case = CoordinatorCase(
        case_id="fu-tier",
        stratum="follow_up",
        context=CoordinatorContext(turn_id="t-fu", question="add the warehouse"),
        required_actions=("query_business", "finish_answer"),
        expected_question_origin="model",
        provenance="spec 2026-09-18 §4.1",
    )
    run = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        question_origin="model",
        continuation_tier="planned",
        answer_text="ok",
    )
    result = score(case, run)
    assert result.trajectory == "fail"
    assert "continuation_tier_not_patch" in result.invariant_violations


def test_score_requires_continues_subject_match_last_business_candidate() -> None:
    """Oracle: continues.subject must equal the last business candidate restore_ref."""
    from app.conversation.coordinator.contracts import CoordinatorContext
    from app.conversation.followup_contracts import FollowupFocus, SourceCandidate
    from app.eval.conversation_coordinator import (
        CoordinatorCase,
        CoordinatorRunOutput,
        score,
    )

    candidate = SourceCandidate(
        position=1,
        exchange_id="ex-1",
        restore_ref="aq-" + "a" * 30,
        grain="entity_rows",
        user_question="list the latest 10 receipts",
    )
    case = CoordinatorCase(
        case_id="fu-subject",
        stratum="follow_up",
        context=CoordinatorContext(
            turn_id="t-fu",
            question="add the warehouse",
            candidates=(candidate,),
            focus=FollowupFocus(status="resolved", source_positions=(1,)),
        ),
        required_actions=("query_business", "finish_answer"),
        expected_question_origin="model",
        provenance="spec 2026-09-18 §4.2",
    )
    mismatch = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        question_origin="model",
        continuation_tier="patch",
        continues_subject="aq-" + "b" * 30,
        answer_text="ok",
    )
    failed = score(case, mismatch)
    assert failed.trajectory == "fail"
    assert "continues_subject_mismatch" in failed.invariant_violations

    matched = CoordinatorRunOutput(
        actions=("query_business", "finish_answer"),
        question_origin="model",
        continuation_tier="patch",
        continues_subject=candidate.restore_ref,
        answer_text="ok",
    )
    passed = score(case, matched)
    assert passed.trajectory == "pass"
    assert passed.invariant_violations == ()


def test_follow_up_cases_load_from_jsonl() -> None:
    """Oracle: evals/conversation_coordinator/cases.jsonl follow-up matrix."""
    from pathlib import Path

    from app.eval.conversation_coordinator import load_coordinator_cases

    root = Path(__file__).resolve().parents[2]
    path = root / "evals" / "conversation_coordinator" / "cases.jsonl"
    cases = load_coordinator_cases(path)
    ids = {c.case_id for c in cases}
    expected = {
        "fu-add-col-receipts",
        "fu-more-rows-receipts",
        "fu-change-sort-receipts",
        "fu-change-entity-receipts",
        "fu-explain-after-receipts",
        "fu-add-col-invoices",
        "fu-more-rows-invoices",
        "fu-change-sort-invoices",
        "fu-change-entity-invoices",
        "fu-explain-after-invoices",
        "fu-add-col-orders",
        "fu-more-rows-orders",
        "fu-change-sort-orders",
        "fu-change-entity-orders",
        "fu-explain-after-orders",
    }
    assert expected <= ids
    patches = [c for c in cases if c.case_id in expected and "change-entity" not in c.case_id]
    edits = [c for c in patches if "explain-after" not in c.case_id]
    assert all(c.expected_question_origin == "model" for c in edits)
    assert all(c.stratum == "follow_up" for c in cases if c.case_id in expected)


_COORDINATOR_FOLDER_TO_TIER = {
    "all": "all",
    "sales": "sales",
    "finance": "finance",
    "admin": "admin",
    "printing": "printing",
    "graphic-design": "graphic design",
    "warehouse": "warehouse",
}


def test_document_cases_cite_a_manual_their_principal_can_read() -> None:
    path = REPO_ROOT / "evals" / "conversation_coordinator" / "cases.jsonl"
    cases = load_coordinator_cases(path)
    doc_cases = [c for c in cases if any(s.endswith(".md") for s in c.expected_sources)]
    assert doc_cases, "Expected coordinator document cases"

    source_re = re.compile(
        r"^(all|sales|finance|admin|printing|graphic-design|warehouse)/manuals/[a-z0-9-]+\.md$"
    )
    corpus_root = REPO_ROOT / "data" / "corpus" / "company"

    for case in doc_cases:
        assert case.principal is not None, f"{case.case_id}: principal is None"
        assert case.principal in PROFILES, (
            f"{case.case_id}: principal '{case.principal}' not in PROFILES"
        )
        profile = PROFILES[case.principal]
        granted_tiers = set(get_access_tiers(profile.role, list(profile.permissions)))

        readable_paths: list[Path] = []
        for p in corpus_root.glob("*/manuals/*.md"):
            folder = p.parent.parent.name
            if _COORDINATOR_FOLDER_TO_TIER.get(folder) in granted_tiers:
                readable_paths.append(p)

        assert case.expected_sources, f"{case.case_id}: expected_sources is empty"
        for src in case.expected_sources:
            assert source_re.match(src), f"{case.case_id}: source '{src}' does not match pattern"
            source_file = corpus_root / src
            assert source_file.is_file(), f"{case.case_id}: source file '{src}' not found on disk"
            tier_folder = src.split("/")[0]
            assert _COORDINATOR_FOLDER_TO_TIER.get(tier_folder) in granted_tiers, (
                f"{case.case_id}: source tier '{tier_folder}' not in granted {granted_tiers}"
            )

        assert case.answer_oracle is not None, f"{case.case_id}: answer_oracle is None"
        oracle_norm = normalize_text(case.answer_oracle)

        for src in case.expected_sources:
            cited_text = normalize_text((corpus_root / src).read_text(encoding="utf-8"))
            assert oracle_norm in cited_text, (
                f"{case.case_id}: oracle '{case.answer_oracle}' not in cited manual {src}"
            )

        allowed_files = {corpus_root / src for src in case.expected_sources}
        for m_path in readable_paths:
            if m_path not in allowed_files:
                m_norm = normalize_text(m_path.read_text(encoding="utf-8"))
                assert oracle_norm not in m_norm, (
                    f"{case.case_id}: oracle '{case.answer_oracle}' leaked to {m_path.name}"
                )
