"""Dataset lint for the SQL dev suite.

Two properties the 2026-08 campaign analysis showed the suite silently violated:
a clarification could never earn credit (no case carried the tag or an oracle),
and two cases had empty gold, which `compare` scores as a match for ANY query
returning zero rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_CASES_PATH = Path("evals/sql/cases.jsonl")

# Gold depends on actual_delivery_date / delivery_date, both 100% NULL, so gold
# returns empty and compare([], [], ...) matches any query that returns nothing.
_DEGENERATE_EMPTY_GOLD_IDS = frozenset({"prod-jobs-delayed", "prod-dept-slowest"})

# Questions a careful analyst cannot answer without picking an interpretation.
# Each must be able to score a clarification: an oracle reply to continue with,
# or the clarify-ok tag when asking IS the right answer.
_AMBIGUOUS_IDS = frozenset({"fin-ar-aging", "prod-revenue-june9", "prod-owes-most"})

_EXPECTED_CASE_COUNT = 42


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    lines = _CASES_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_case_count_is_pinned(cases: list[dict]) -> None:
    """Catches a silent re-add of a removed case as well as an accidental drop."""
    assert len(cases) == _EXPECTED_CASE_COUNT


def test_case_ids_are_unique(cases: list[dict]) -> None:
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))


def test_degenerate_empty_gold_cases_are_absent(cases: list[dict]) -> None:
    """Empty gold is not evidence -- ANY query returning nothing scored a match.

    Removed rather than scored `match=None`: a non-bool match classifies as
    `no_execution_score`, which is NOT exempt from the 2% opportunity-failure
    gate, so 2 cases x 5 repeats x 2 arms = 4.5% would abort every campaign.
    """
    present = {c["id"] for c in cases} & _DEGENERATE_EMPTY_GOLD_IDS
    assert not present, f"empty-gold cases must stay removed: {sorted(present)}"


@pytest.mark.parametrize("case_id", sorted(_AMBIGUOUS_IDS))
def test_ambiguous_cases_can_score_a_clarification(cases: list[dict], case_id: str) -> None:
    by_id = {c["id"]: c for c in cases}
    assert case_id in by_id, f"{case_id} missing from the suite"
    case = by_id[case_id]
    has_oracle = bool(case.get("clarify_answer"))
    has_tag = "clarify-ok" in {str(t).lower() for t in case.get("tags") or []}
    assert has_oracle or has_tag, (
        f"{case_id} is ambiguous but a clarification scores as no_query -- "
        "give it a clarify_answer oracle or the clarify-ok tag"
    )


def test_oracle_and_clarify_ok_are_mutually_exclusive(cases: list[dict]) -> None:
    """An oracle means 'reply and require SQL'; clarify-ok means 'asking is the answer'.

    Both on one case makes the expected outcome ambiguous -- score_case disables
    clarify-ok credit whenever an oracle is present, so the tag would be a lie.
    """
    for case in cases:
        tags = {str(t).lower() for t in case.get("tags") or []}
        if "clarify-ok" in tags:
            assert not case.get("clarify_answer"), f"{case['id']} has both"


def test_every_case_documents_a_clarify_choice_in_notes(cases: list[dict]) -> None:
    """A tag or oracle without a stated reason is how gold drifts toward the model."""
    for case in cases:
        tags = {str(t).lower() for t in case.get("tags") or []}
        if case.get("clarify_answer") or "clarify-ok" in tags:
            assert (case.get("notes") or "").strip(), f"{case['id']} needs a notes reason"
