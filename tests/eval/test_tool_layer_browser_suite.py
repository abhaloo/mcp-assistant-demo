"""Freeze validator for the tool-layer BrowserOS case map (task E1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval.build_tool_layer_browser_suite import (
    build_tool_layer_browser_suite,
    validate_case_oracles,
)

REPO = Path(__file__).resolve().parents[2]
CASE_MAP = REPO / "evals" / "tool_layer" / "browser-case-map.json"


def test_required_case_cannot_freeze_without_an_oracle() -> None:
    with pytest.raises(ValueError, match="missing oracle: tl-hybrid-partial"):
        validate_case_oracles(
            [{"id": "tl-hybrid-partial", "required": True, "oracle_id": "partial-1"}],
            {},
        )


def test_duplicate_case_ids_fail() -> None:
    with pytest.raises(ValueError, match="duplicate case id"):
        validate_case_oracles(
            [
                {"id": "ac-01", "required": True, "oracle_id": "o1", "acceptance_ids": ["AC01"]},
                {"id": "ac-01", "required": True, "oracle_id": "o1", "acceptance_ids": ["AC01"]},
            ],
            {"o1": {"kind": "independent"}},
        )


def test_unknown_acceptance_id_fails() -> None:
    with pytest.raises(ValueError, match="unknown acceptance id"):
        validate_case_oracles(
            [
                {
                    "id": "ac-01",
                    "required": True,
                    "oracle_id": "o1",
                    "acceptance_ids": ["AC99"],
                }
            ],
            {"o1": {"kind": "independent"}},
        )


def test_case_map_validates_against_placeholder_oracles() -> None:
    cases = json.loads(CASE_MAP.read_text(encoding="utf-8"))
    oracles = {str(c["oracle_id"]): {"kind": "independent"} for c in cases}
    validate_case_oracles(cases, oracles)


def test_builder_refuses_overwrite(tmp_path: Path) -> None:
    cases = json.loads(CASE_MAP.read_text(encoding="utf-8"))
    oracles = {str(c["oracle_id"]): {"kind": "independent"} for c in cases}
    oracle_path = tmp_path / "oracles.json"
    oracle_path.write_text(json.dumps(oracles), encoding="utf-8")
    env_card = tmp_path / "env-card.json"
    env_card.write_text(json.dumps({"slug": "analytics-w2", "verdict": "test"}), encoding="utf-8")
    out = tmp_path / "run"
    build_tool_layer_browser_suite(
        case_map=CASE_MAP,
        env_card=env_card,
        oracle=oracle_path,
        output_dir=out,
    )
    with pytest.raises(FileExistsError):
        build_tool_layer_browser_suite(
            case_map=CASE_MAP,
            env_card=env_card,
            oracle=oracle_path,
            output_dir=out,
        )


def test_executor_plan_omits_protected_oracles(tmp_path: Path) -> None:
    cases = json.loads(CASE_MAP.read_text(encoding="utf-8"))
    oracles = {str(c["oracle_id"]): {"kind": "independent", "secret": "do-not-copy"} for c in cases}
    oracle_path = tmp_path / "oracles.json"
    oracle_path.write_text(json.dumps(oracles), encoding="utf-8")
    env_card = tmp_path / "env-card.json"
    env_card.write_text(json.dumps({"slug": "analytics-w2"}), encoding="utf-8")
    out = tmp_path / "run"
    build_tool_layer_browser_suite(
        case_map=CASE_MAP,
        env_card=env_card,
        oracle=oracle_path,
        output_dir=out,
    )
    plan = json.loads((out / "executor-run-plan.json").read_text(encoding="utf-8"))
    dumped = json.dumps(plan)
    assert "do-not-copy" not in dumped
    assert "secret" not in dumped
    for item in plan["cases"]:
        assert "oracle" not in item
        assert "question_steps" in item
        assert item["evidence_layer"] in {"browseros_live_pair", "paired_deploy"}
