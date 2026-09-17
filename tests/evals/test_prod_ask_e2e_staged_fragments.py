"""Staged fragments for the frozen prod Ask E2E suite.

A fragment holds cases authored while `evals/prod_ask_e2e/suite.json` is frozen
for another plan (D4: planner recovery owns 1.17.0). Oracles here are the frozen
suite at HEAD (ids, personas, dom_contract) and the canonical stripper's
forbidden-token derivation - a fragment must merge without collisions and
without leaking an answer key into the executor's run plan.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SUITE_PATH = REPO / "evals" / "prod_ask_e2e" / "suite.json"
STAGED_DIR = REPO / "evals" / "prod_ask_e2e" / "staged"
STRIPPER = REPO / ".claude" / "skills" / "ai-e2e" / "assets" / "make_run_plan.py"
MERGER = REPO / "scripts" / "eval" / "merge_staged_suite.py"
DERIVED_SET_FRAGMENT = STAGED_DIR / "1.18.0-derived-sets.json"

FRAGMENTS = sorted(STAGED_DIR.glob("*.json")) if STAGED_DIR.is_dir() else []
HEADER_KEYS = {
    "target_suite_version",
    "requires_prior_version",
    "revision_history_entry",
    "cases",
    "dom_contract_additions",
    "release_gates_additions",
    "coverage_gaps_additions",
    "oracle_sql_addendum",
}


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _suite() -> dict[str, Any]:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def _fragment(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fragment_ids() -> list[str]:
    return [p.stem for p in FRAGMENTS]


def test_derived_set_fragment_exists() -> None:
    assert DERIVED_SET_FRAGMENT.is_file()


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_fragment_header_is_complete(path: Path) -> None:
    fragment = _fragment(path)
    assert HEADER_KEYS <= set(fragment)
    target = fragment["target_suite_version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", target)
    assert path.name.startswith(f"{target}-")
    assert fragment["revision_history_entry"].startswith(f"{target} - ")
    assert fragment["cases"], "a fragment with no cases is not a fragment"


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_case_ids_are_new_and_unique(path: Path) -> None:
    frozen_ids = {c["id"] for c in _suite()["cases"]}
    ids = [c["id"] for c in _fragment(path)["cases"]]
    assert len(ids) == len(set(ids))
    assert not frozen_ids & set(ids)


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_cases_name_known_personas_and_required_keys(path: Path) -> None:
    personas = set(_suite()["personas"])
    for case in _fragment(path)["cases"]:
        assert case["persona"] in personas, case["id"]
        for key in ("title", "question", "procedure", "oracle", "checks", "dimensions"):
            assert key in case, f"{case['id']} lacks {key}"
        assert case["checks"], case["id"]
        for check in case["checks"]:
            assert "type" in check, case["id"]


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_number_oracles_carry_an_independent_view_statement(path: Path) -> None:
    fragment = _fragment(path)
    addendum = fragment["oracle_sql_addendum"]
    for case in fragment["cases"]:
        oracle = case["oracle"]
        if oracle.get("kind") != "number_equals":
            continue
        assert isinstance(oracle["value"], int), case["id"]
        assert "ai_v1_bq_" in oracle["statement"], case["id"]
        assert case["id"] in addendum, f"{case['id']} has no labelled statement in the addendum"


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_pairs_and_cross_case_refer_to_existing_ids(path: Path) -> None:
    fragment = _fragment(path)
    known = {c["id"] for c in _suite()["cases"]} | {c["id"] for c in fragment["cases"]}
    for case in fragment["cases"]:
        for pair in case.get("pairs_with", []):
            assert pair in known, f"{case['id']} pairs with unknown {pair}"
        for check in case["checks"]:
            if check["type"] == "cross_case":
                for ref in re.findall(r"\b[a-z]{2,3}-\d{2}[a-z]?\b", check["assert"]):
                    assert ref in known, f"{case['id']} cross_case names unknown {ref}"


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_dom_contract_additions_do_not_overwrite_frozen_keys(path: Path) -> None:
    frozen = set(_suite()["dom_contract"])
    additions = set(_fragment(path)["dom_contract_additions"])
    assert not frozen & additions


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_merged_suite_bumps_version_and_appends_history(path: Path) -> None:
    merger = _load_module(MERGER, "merge_staged_suite")
    fragment = _fragment(path)
    suite = _suite()
    suite["suite_version"] = fragment["requires_prior_version"]
    merged = merger.merge(suite, fragment)
    assert merged["suite_version"] == fragment["target_suite_version"]
    assert merged["revision_history"][-1] == fragment["revision_history_entry"]
    assert len(merged["cases"]) == len(suite["cases"]) + len(fragment["cases"])
    assert len(merged["release_gates"]) == (
        len(suite["release_gates"]) + len(fragment["release_gates_additions"])
    )
    assert len(merged["coverage_gaps"]["gaps"]) == (
        len(suite["coverage_gaps"]["gaps"]) + len(fragment["coverage_gaps_additions"])
    )


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_merge_refuses_the_wrong_prior_version_and_a_second_merge(path: Path) -> None:
    merger = _load_module(MERGER, "merge_staged_suite")
    fragment = _fragment(path)
    suite = _suite()
    suite["suite_version"] = "0.0.0"
    with pytest.raises(ValueError, match="requires_prior_version"):
        merger.merge(suite, fragment)
    suite["suite_version"] = fragment["requires_prior_version"]
    merged = merger.merge(suite, fragment)
    with pytest.raises(ValueError, match="requires_prior_version"):
        merger.merge(merged, fragment)
    merged["suite_version"] = fragment["requires_prior_version"]
    with pytest.raises(ValueError, match="already"):
        merger.merge(merged, fragment)


@pytest.mark.parametrize("path", FRAGMENTS, ids=_fragment_ids())
def test_merged_suite_strips_without_leaking_an_answer(path: Path) -> None:
    merger = _load_module(MERGER, "merge_staged_suite")
    stripper = _load_module(STRIPPER, "prod_ask_e2e_make_run_plan")
    fragment = _fragment(path)
    suite = _suite()
    suite["suite_version"] = fragment["requires_prior_version"]
    merged = merger.merge(suite, fragment)
    plan = stripper.build(merged)
    blob = json.dumps(plan, indent=2)
    assert stripper.leaked_tokens(merged, blob) == []
    plan_ids = {c["id"] for c in plan["cases"]}
    for case in fragment["cases"]:
        assert case["id"] in plan_ids
        stripped = next(c for c in plan["cases"] if c["id"] == case["id"])
        for key in ("oracle", "checks", "title", "expected_route", "escalate_if"):
            assert key not in stripped, f"{case['id']} leaks {key}"
