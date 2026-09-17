"""The judged layer is exercised with a scripted judge, never a live model."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.eval.ask_route.judge import (
    ACCURACY_ANCHORS,
    HELPFULNESS_ANCHORS,
    JudgeProtocolError,
    JudgeRequest,
    build_prompt,
    canary_holds,
    judge_one,
    load_canary,
    parse_verdict,
    run_judge_canary,
)

CANARY_PATH = Path("evals/prod_ask/judge-canary.jsonl")


def test_every_score_on_both_scales_has_an_anchor():
    assert sorted(ACCURACY_ANCHORS) == [1, 2, 3, 4, 5]
    assert sorted(HELPFULNESS_ANCHORS) == [1, 2, 3, 4, 5]
    for text in list(ACCURACY_ANCHORS.values()) + list(HELPFULNESS_ANCHORS.values()):
        assert len(text.split()) >= 12, "an anchor that short is a label, not a criterion"


def test_accuracy_prompt_carries_the_evidence_and_the_scale():
    system, user = build_prompt(
        JudgeRequest(
            case_id="pa-01",
            dimension="accuracy",
            question="How much?",
            answer="Tsh 140.",
            evidence="1,000 cards: Tsh 140 single-sided",
            claim="the single-sided price",
        )
    )
    assert "never see the documents the assistant retrieved" in system
    assert "1,000 cards: Tsh 140 single-sided" in user
    assert "the single-sided price" in user
    for anchor in ACCURACY_ANCHORS.values():
        assert anchor in user


def test_helpfulness_prompt_lists_the_needs_it_grades_against():
    _system, user = build_prompt(
        JudgeRequest(
            case_id="pa-03",
            dimension="helpfulness",
            question="Which suppliers?",
            answer="A table.",
            evidence="ignored for helpfulness",
            user_need="schedule payments",
            must_enable=("see every supplier", "see each amount"),
        )
    )
    assert "schedule payments" in user
    assert "- see every supplier" in user
    assert "- see each amount" in user


@pytest.mark.parametrize(
    "raw",
    [
        '{"score": 4, "evidence_quote": "Tsh 140", "reason": "matches"}',
        '```json\n{"score": 4, "evidence_quote": "Tsh 140", "reason": "matches"}\n```',
        'Sure. {"score": 4, "evidence_quote": "Tsh 140", "reason": "matches"} Hope that helps.',
    ],
    ids=["plain", "fenced", "surrounded-by-prose"],
)
def test_verdict_parsing_accepts_the_shapes_a_model_actually_emits(raw):
    assert parse_verdict(raw).score == 4


@pytest.mark.parametrize(
    "raw",
    ["the answer looks fine", '{"score": 9}', '{"score": "four"}', "{not json}"],
    ids=["prose-only", "out-of-range", "wrong-type", "broken-json"],
)
def test_unparsable_verdicts_raise_rather_than_default_to_a_score(raw):
    with pytest.raises(JudgeProtocolError):
        parse_verdict(raw)


def test_judge_one_uses_the_injected_completion():
    calls: list[tuple[str, str]] = []

    def complete(system: str, user: str) -> str:
        calls.append((system, user))
        return '{"score": 5, "evidence_quote": "x", "reason": "y"}'

    verdict = judge_one(
        JudgeRequest(
            case_id="pa-01",
            dimension="accuracy",
            question="q",
            answer="a",
            evidence="e",
        ),
        complete,
    )
    assert verdict.score == 5
    assert len(calls) == 1


def test_canary_file_covers_both_dimensions_and_the_scale_ends():
    cases = load_canary(CANARY_PATH)
    dimensions = {case.dimension for case in cases}
    assert dimensions == {"accuracy", "helpfulness"}
    for dimension in dimensions:
        scores = {c.expected_score for c in cases if c.dimension == dimension}
        assert 1 in scores and 5 in scores, f"{dimension} canary must pin both ends"


def test_a_faithful_judge_holds_the_canary():
    cases = load_canary(CANARY_PATH)
    labels = {case.answer: case.expected_score for case in cases}

    def complete(_system: str, user: str) -> str:
        score = next(value for answer, value in labels.items() if answer in user)
        return f'{{"score": {score}, "evidence_quote": "q", "reason": "r"}}'

    outcomes = run_judge_canary(cases, complete)
    assert canary_holds(outcomes)


def test_a_judge_that_drifts_two_points_voids_the_run():
    cases = load_canary(CANARY_PATH)

    def complete(_system: str, _user: str) -> str:
        return '{"score": 3, "evidence_quote": "q", "reason": "r"}'

    outcomes = run_judge_canary(cases, complete)
    assert not canary_holds(outcomes)
    assert any(not outcome.within_tolerance for outcome in outcomes)


def test_one_point_drift_is_tolerated():
    cases = [case for case in load_canary(CANARY_PATH) if case.expected_score == 5]

    def complete(_system: str, _user: str) -> str:
        return '{"score": 4, "evidence_quote": "q", "reason": "r"}'

    assert canary_holds(run_judge_canary(cases, complete))
