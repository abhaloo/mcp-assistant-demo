"""Forbidden-token leak check of the prod Ask E2E run-plan stripper.

Oracle: a hand-built fixture suite. A three-digit oracle value must be caught
when it stands alone in the executor's plan, and must NOT be caught when it is
merely a substring of an unrelated identifier (a manifest hash, a port, a job
id). The frozen suite's known_conditions text carries the legacy manifest hash
7a413053, which contains 413 - the ds-02 oracle - so a substring check would
refuse to build the plan for a correct suite.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parents[2]
STRIPPER = REPO / ".claude" / "skills" / "ai-e2e" / "assets" / "make_run_plan.py"


def _stripper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prod_ask_e2e_make_run_plan", STRIPPER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(known_condition: str, procedure: str) -> dict:
    return {
        "suite_id": "fixture",
        "suite_version": "0",
        "environment": {"app_url": "http://example.test", "known_conditions": [known_condition]},
        "preflight": {},
        "personas": {},
        "dom_contract": {},
        "cases": [
            {
                "id": "x-01",
                "question": "how many open orders are outside the top ten?",
                "procedure": procedure,
                "oracle": {"kind": "number_equals", "value": 413},
                "checks": [{"type": "numbers_absent", "values": ["502"]}],
            }
        ],
    }


def test_oracle_value_inside_an_unrelated_hash_is_not_a_leak() -> None:
    stripper = _stripper()
    suite = _fixture("legacy manifest hash 7a413053 is accepted", "Send the question.")
    assert "413" in stripper.forbidden_tokens(suite)
    blob = json.dumps(stripper.build(suite), indent=2)
    assert stripper.leaked_tokens(suite, blob) == []


def test_oracle_value_standing_alone_in_the_plan_is_a_leak() -> None:
    # The leak sits in environment prose, which the executor reads. A number in
    # `procedure` is a typed INPUT and is deliberately exempt (see forbidden_tokens).
    stripper = _stripper()
    suite = _fixture("413 open orders sit outside the top ten this week", "Send the question.")
    blob = json.dumps(stripper.build(suite), indent=2)
    assert stripper.leaked_tokens(suite, blob) == ["413"]


def test_grouped_and_ungrouped_forms_are_both_checked() -> None:
    stripper = _stripper()
    suite = _fixture("the unscoped total is 6,113 - never say so", "Send the question.")
    suite["cases"][0]["oracle"]["value"] = 6113
    blob = json.dumps(stripper.build(suite), indent=2)
    assert stripper.leaked_tokens(suite, blob) == ["6,113"]


def test_typed_input_numbers_stay_exempt() -> None:
    stripper = _stripper()
    suite = _fixture("nothing special", "Expect 413 rows, then record the receipt.")
    blob = json.dumps(stripper.build(suite), indent=2)
    assert stripper.leaked_tokens(suite, blob) == []
