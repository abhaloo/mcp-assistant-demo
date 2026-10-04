"""Prod Ask E2E run-plan stripper and suite timeout-contract invariants.

Oracle for mp-01/mp-02: git HEAD (or parent product SHA) suite.json, not the
working-tree file after this revision. Oracle for suite literals: ADR 0070
and the timeout-remediation Success Contract (AC-T3 stall card with LLM choices).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from app.eval.ask_route.text import contains_phrase, normalize_text

REPO = Path(__file__).resolve().parents[2]
SUITE_PATH = REPO / "evals" / "prod_ask_e2e" / "suite.json"
CANONICAL_STRIPPER = REPO / ".claude" / "skills" / "ai-e2e" / "assets" / "make_run_plan.py"
CLI_LAUNCHER = REPO / "scripts" / "eval" / "make_prod_ask_e2e_run_plan.py"
PARENT_PRODUCT_SHA = "c6c533cf742f877ee51cc045959f02c254bbd9ee"
NEXT_SUITE_VERSION = "1.25.0"

MANUAL_RE = re.compile(
    r"^data/corpus/company/(all|sales|finance|admin|printing|graphic-design|warehouse)/manuals/[a-z0-9-]+\.md$"
)

FOLDER_TO_TIER = {
    "all": "all",
    "sales": "sales",
    "finance": "finance",
    "admin": "admin",
    "printing": "printing",
    "graphic-design": "graphic design",
    "warehouse": "warehouse",
}


def _load_stripper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prod_ask_e2e_make_run_plan", CANONICAL_STRIPPER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_suite(ref: str) -> dict[str, Any]:
    blob = subprocess.check_output(
        ["git", "show", f"{ref}:evals/prod_ask_e2e/suite.json"],
        cwd=REPO,
    )
    return json.loads(blob)


def _parent_suite() -> dict[str, Any]:
    try:
        return _git_suite("HEAD")
    except subprocess.CalledProcessError:
        return _git_suite(PARENT_PRODUCT_SHA)


def _case(suite: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(c for c in suite["cases"] if c["id"] == case_id)


def test_suite_version_and_revision_history_name_adr_0070_and_cl_05() -> None:
    suite = _load_suite()
    assert suite["suite_version"] == NEXT_SUITE_VERSION
    history = suite["revision_history"]
    assert history[-1].startswith(f"{NEXT_SUITE_VERSION} -")
    entry = next(h for h in history if "ADR 0070" in h)
    assert "ADR 0070" in entry
    assert "cl-04" in entry
    assert "cl-05" in entry
    assert "taking longer than usual" in entry
    assert ".mcp-ask-v2__card--clarification" in entry
    assert "chip count exactly 0" in entry
    assert "free text" in entry
    assert "stalled step sealed failed" in entry
    assert "zero recovery model calls" in entry
    assert "supersession" in entry


def test_cl_04_remains_in_source_with_historical_body_and_is_superseded() -> None:
    suite = _load_suite()
    parent = _parent_suite()
    current = _case(suite, "cl-04")
    historical = _case(parent, "cl-04")
    assert current["status"] == "superseded"
    assert current["superseded_by"] == (
        "cl-05 (ADR 0070: stall card without model-derived choices)"
    )
    for key in ("question", "procedure", "oracle", "checks"):
        assert current[key] == historical[key]


def test_cl_05_is_conditional_stall_card_with_llm_chips() -> None:
    cl_05 = _case(_load_suite(), "cl-05")
    assert "CONDITIONAL" in cl_05["procedure"]
    check_types = {c["type"]: c for c in cl_05["checks"]}
    assert check_types["text_includes"]["value"] == "taking longer than usual"
    assert check_types["card_rendered"]["selector"] == ".mcp-ask-v2__card--clarification"
    chip = check_types["chip_count"]
    assert chip.get("min") == 1
    assert chip.get("max") == 3
    assert any("free text" in json.dumps(c).lower() for c in cl_05["checks"])
    assert any(c["type"] == "no_step_left_running" for c in cl_05["checks"])


def test_mp_01_and_mp_02_oracles_match_parent_byte_for_byte() -> None:
    live = _load_suite()
    parent = _parent_suite()
    for case_id in ("mp-01", "mp-02"):
        assert _case(live, case_id)["oracle"] == _case(parent, case_id)["oracle"]


def test_superseded_and_run_with_cases_stay_in_suite_and_leave_the_run_plan() -> None:
    stripper = _load_stripper()
    suite = _load_suite()
    ids_in_suite = {c["id"] for c in suite["cases"]}
    assert "cl-04" in ids_in_suite
    assert "cl-05" in ids_in_suite
    plan = stripper.build(suite)
    plan_ids = [c["id"] for c in plan["cases"]]
    assert "cl-04" not in plan_ids
    assert "cl-05" in plan_ids
    assert "run_with" not in _case(suite, "cl-05")
    assert "CONDITIONAL" in next(c for c in plan["cases"] if c["id"] == "cl-05")["procedure"]
    fixture = {
        "suite_id": "fixture",
        "suite_version": "0",
        "environment": {"app_url": "http://example.test"},
        "preflight": {},
        "personas": {},
        "dom_contract": {},
        "cases": [
            {"id": "keep", "question": "q"},
            {"id": "old", "question": "q", "status": "superseded"},
            {"id": "scripted", "question": "q", "run_with": "scripts/smoke/x.py"},
        ],
    }
    built_ids = [c["id"] for c in stripper.build(fixture)["cases"]]
    assert built_ids == ["keep"]


def test_planner_stall_ceiling_names_llm_choices_not_empty_card() -> None:
    text = _load_suite()["coverage_gaps"]["planner_stall_ceiling"]
    assert "narrowing" not in text
    assert "choices=[]" not in text
    assert "LLM" in text or "1 to 3" in text
    assert "planner-timeout" in text
    assert "typed timeout" in text
    assert "not forceable from the browser" in text


def test_current_coverage_gaps_do_not_cite_test_narrowing() -> None:
    gaps = _load_suite()["coverage_gaps"]["gaps"]
    current = [g for g in gaps if g.get("status") != "superseded"]
    cited = [g for g in current if "test_narrowing.py" in json.dumps(g)]
    assert cited == []
    stall = next(g for g in current if "cl-05" in json.dumps(g))
    blob = json.dumps(stall)
    assert "tests/business_query/test_s1_resolver.py" in blob
    assert "test_narrowing.py" not in blob


def test_evals_prod_ask_e2e_has_no_stripper_python() -> None:
    assert not (REPO / "evals" / "prod_ask_e2e" / "make_run_plan.py").exists()


def test_mp_cases_in_run_plan_have_oracle_keys_stripped() -> None:
    stripper = _load_stripper()
    plan = stripper.build(_load_suite())
    by_id = {c["id"]: c for c in plan["cases"]}
    for case_id in ("mp-01", "mp-02"):
        case = by_id[case_id]
        for key in ("oracle", "checks", "title", "expected_route"):
            assert key not in case
        assert "question" in case
        assert "procedure" in case


def test_ef_05_unavailable_message_resolves_from_runtime_import() -> None:
    import importlib

    from app.conversation.coordinator.runtime import EXPLANATION_UNAVAILABLE_MESSAGE

    ef05 = _case(_load_suite(), "ef-05")
    path = ef05["oracle"]["runtime_message_import"]
    mod_name, attr = path.rsplit(".", 1)
    resolved = getattr(importlib.import_module(mod_name), attr)
    assert resolved == EXPLANATION_UNAVAILABLE_MESSAGE
    includes = [c for c in ef05["checks"] if c["type"] == "text_includes"]
    assert includes
    assert includes[0]["import"] == path
    assert not any(
        c["type"] == "diagnostic_observation" for c in _case(_load_suite(), "ef-04")["checks"]
    )
    assert not any(
        c["type"] == "diagnostic_observation" for c in _case(_load_suite(), "ef-07")["checks"]
    )


def test_r9_suite_checks_encode_validation_oracles() -> None:
    suite = _load_suite()
    co06 = json.dumps(_case(suite, "co-06")["checks"])
    assert "reject_before_prohibited_call" in co06
    assert "stop_reason" in co06
    assert "business_query_limit" in co06
    assert "admitted" in co06
    assert "completed" in co06
    assert "rejected" in co06
    co12 = json.dumps(_case(suite, "co-12")["checks"])
    assert "one_complete_per_admitted" in co12
    assert "missing_complete_unproven" in co12
    ef07 = json.dumps(_case(suite, "ef-07")["checks"])
    assert "source_exchange_ids" in ef07
    ef05 = _case(suite, "ef-05")
    assert (
        ef05["oracle"]["runtime_message_import"]
        == "app.conversation.coordinator.runtime.EXPLANATION_UNAVAILABLE_MESSAGE"
    )
    from scripts.eval.conversation_suite_grade import GRADED_CHECK_KEYS, grade_turn_invocation_check

    required = (
        "replacement_exchanges",
        "no_new_call_after_cancel",
        "outcome_type",
        "answer_modes",
        "first_two_work_steps",
        "source_exchange_ids_min",
        "source_matches_persisted",
    )
    for key in required:
        assert key in GRADED_CHECK_KEYS
        probe: dict[str, Any] = {"type": "turn_invocation_report"}
        empty: dict[str, Any] = {"counts": {}, "actions": {}}
        if key == "answer_modes":
            probe[key] = ["direct"]
        elif key == "first_two_work_steps":
            probe[key] = 0
        elif key == "source_exchange_ids_min":
            probe[key] = 1
        elif key == "replacement_exchanges":
            probe[key] = 1
        elif key == "no_new_call_after_cancel":
            probe[key] = True
            empty["new_call_after_cancel"] = True
        else:
            probe[key] = True
        assert grade_turn_invocation_check(empty, probe), key
        for case in suite["cases"]:
            case_id = str(case.get("id") or "")
            if not (case_id.startswith("co-") or case_id in {"ef-04", "ef-05", "ef-06", "ef-07"}):
                continue
            for check in case.get("checks") or []:
                if check.get("type") != "turn_invocation_report":
                    continue
                for key in check:
                    if key == "type":
                        continue
                    assert key in GRADED_CHECK_KEYS, f"{case_id} ungraded {key}"
    for case_id in ("ef-04", "ef-06", "co-01", "co-04", "co-09"):
        blob = json.dumps(_case(suite, case_id)["checks"])
        assert "turn_invocation_report" in blob
        assert "coordinator_min" not in blob or case_id == "keep"


def test_cli_launcher_writes_the_same_bytes_as_the_asset(tmp_path: Path) -> None:
    asset_out = tmp_path / "from-asset.json"
    cli_out = tmp_path / "from-cli.json"
    subprocess.check_call(
        [
            sys.executable,
            str(CANONICAL_STRIPPER),
            "--suite",
            str(SUITE_PATH),
            "--out",
            str(asset_out),
        ],
        cwd=REPO,
    )
    subprocess.check_call(
        [
            sys.executable,
            str(CLI_LAUNCHER),
            "--suite",
            str(SUITE_PATH),
            "--out",
            str(cli_out),
        ],
        cwd=REPO,
    )
    assert asset_out.read_bytes() == cli_out.read_bytes()


def test_env_card_overrides_environment_endpoints_and_database() -> None:
    """The suite freezes oracles, not a machine. The executor's plan takes its
    endpoints and database from the env card of the slug it runs against."""
    stripper = _load_stripper()
    fixture = {
        "suite_id": "fixture",
        "suite_version": "0",
        "environment": {
            "app_url": "http://127.0.0.1:8158",
            "rag_api": "http://127.0.0.1:8358",
            "database": "mcp_prod_wire_eval",
            "entity_id": 1,
        },
        "preflight": {},
        "personas": {},
        "dom_contract": {},
        "cases": [{"id": "keep", "question": "q"}],
    }
    card = {"ports": {"app": 8232, "rag": 8432}, "db": "mcp_analytics_w2"}
    env = stripper.build(fixture, env_card=card)["environment"]
    assert env["app_url"] == "http://127.0.0.1:8232"
    assert env["rag_api"] == "http://127.0.0.1:8432"
    assert env["database"] == "mcp_analytics_w2"
    assert env["entity_id"] == 1


def test_st_stop_cases_are_in_the_suite() -> None:
    """Oracle: spec §4.6 Stop control — st-01..st-05 stop / reopen / next question."""
    suite = _load_suite()
    ids = [_case(suite, cid)["id"] for cid in ("st-01", "st-02", "st-03", "st-04", "st-05")]
    assert ids == ["st-01", "st-02", "st-03", "st-04", "st-05"]
    st01 = _case(suite, "st-01")
    assert st01["persona"] == "sales"
    blob = json.dumps(st01["checks"])
    assert "running" in blob
    assert "250" in blob


def _manuals_with_tiers() -> list[tuple[Path, str]]:
    base = REPO / "data" / "corpus" / "company"
    results: list[tuple[Path, str]] = []
    for tier_dir in base.iterdir():
        if not tier_dir.is_dir():
            continue
        manuals_dir = tier_dir / "manuals"
        if not manuals_dir.is_dir():
            continue
        tier = FOLDER_TO_TIER.get(tier_dir.name, tier_dir.name)
        results.extend((md, tier) for md in manuals_dir.glob("*.md"))
    return results


def test_document_oracles_cite_manuals_that_contain_their_facts() -> None:
    suite = _load_suite()
    doc_cases = [
        c
        for c in suite["cases"]
        if isinstance(c.get("oracle"), dict) and c["oracle"].get("kind") == "document_facts"
    ]
    assert len(doc_cases) >= 4
    for case in doc_cases:
        source = case["oracle"]["source"]
        assert MANUAL_RE.match(source), f"{case['id']}: {source} does not match MANUAL_RE"
        source_path = REPO / source
        assert source_path.is_file(), f"{case['id']}: {source} does not exist"
        text = source_path.read_text(encoding="utf-8")
        literals = list(case["oracle"].get("must_include", [])) + list(
            case["oracle"].get("must_include_any", [])
        )
        for lit in literals:
            assert contains_phrase(text, lit), (
                f"{case['id']}: literal '{lit}' not found in {source}"
            )


def _assert_pair_literals(
    allow: dict[str, Any],
    deny: dict[str, Any],
    allow_tiers: set[str],
    deny_tiers: set[str],
    manuals: list[tuple[Path, str]],
) -> None:
    allow_id, deny_id = allow["id"], deny["id"]
    allow_source = REPO / allow["oracle"]["source"]
    assert allow_source.is_file(), f"{allow_id}: source {allow['oracle']['source']} does not exist"
    allow_text = allow_source.read_text(encoding="utf-8")

    allow_literals = list(allow["oracle"].get("must_include", [])) + list(
        allow["oracle"].get("must_include_any", [])
    )
    deny_literals: list[str] = []
    for check in deny.get("checks", []):
        if check.get("type") == "numbers_absent" and "values" in check:
            deny_literals.extend(check["values"])

    assert allow_literals, f"{allow_id} has no must_include/must_include_any literals"
    assert deny_literals, f"{deny_id} has no numbers_absent values"

    for lit in allow_literals + deny_literals:
        assert contains_phrase(allow_text, lit), (
            f"{allow_id}/{deny_id}: literal '{lit}' not in ALLOW source {allow_source.name}"
        )
        for manual_path, manual_tier in manuals:
            if manual_tier in deny_tiers:
                assert not contains_phrase(manual_path.read_text(encoding="utf-8"), lit), (
                    f"{deny_id}: literal '{lit}' found in DENY manual {manual_path.name}"
                )

    for lit in allow_literals:
        for manual_path, manual_tier in manuals:
            if manual_path != allow_source and manual_tier in allow_tiers:
                assert not contains_phrase(manual_path.read_text(encoding="utf-8"), lit), (
                    f"{allow_id}: ALLOW literal '{lit}' also found in {manual_path.name}"
                )

    for lit in deny_literals:
        assert normalize_text(lit) not in normalize_text(deny["question"]), (
            f"{deny_id}: DENY literal '{lit}' is a substring of the question"
        )


def test_document_pairs_land_opposite_ways_by_construction() -> None:
    suite = _load_suite()
    manuals = _manuals_with_tiers()
    pairs = [
        ("rb-07", "rb-08"),
        ("rb-09", "rb-10"),
        ("rb-12", "rb-11"),
    ]
    for allow_id, deny_id in pairs:
        allow = _case(suite, allow_id)
        deny = _case(suite, deny_id)
        deny_tiers = set(suite["personas"][deny["persona"]]["document_tiers"])
        allow_tiers = set(suite["personas"][allow["persona"]]["document_tiers"])
        _assert_pair_literals(allow, deny, allow_tiers, deny_tiers, manuals)


def _check(case_id: str, check_type: str) -> dict[str, Any]:
    case = _case(_load_suite(), case_id)
    return next(c for c in case["checks"] if c["type"] == check_type)


def test_v2_table_selector_on_ac03() -> None:
    selector = _check("ac-03", "dom_present")["selector"]
    assert "[data-slot='result'] table.mcp-ask-v2__table" in selector


def test_v2_work_block_and_actions_on_ux02() -> None:
    phases = _check("ux-02", "phases_visible")
    assert phases["selector"] == "details.mcp-ask-v2__work[data-mcp-ask-v2-work]"
    assert ".mcp-ask-v2__step-state" in phases["capture"] and "Running" in phases["capture"]
    assert _check("ux-02", "actions_rendered")["selector"] == "[data-copy]"


def test_repointed_personas_hold_view_job() -> None:
    for case_id in ("iso-01", "mb-01", "rc-01", "rc-02", "fu-05", "cp-01"):
        assert _case(_load_suite(), case_id)["persona"] == "admin"


def test_mb01_option_height_applies_only_with_a_card() -> None:
    assert "card" in _check("mb-01", "element_min_height")["conditional_on"]


def test_fu01_is_superseded_by_fu04() -> None:
    case = _case(_load_suite(), "fu-01")
    assert case["status"] == "superseded"
    assert case["superseded_by"] == "fu-04"


def test_fu02_clicks_a_follow_up_option_after_hp01() -> None:
    case = _case(_load_suite(), "fu-02")
    assert case["conversation"] == "reuse:hp-01"
    assert "ASK_AI_V2_MODULE_INTERACTION_CARD" not in case["procedure"]
    assert "NOT RUN (precondition)" in case["procedure"]
    types = [c["type"] for c in case["checks"]]
    new = (
        "follow_up_option_click_sends",
        "reply_not_refused",
        "follow_up_option_count",
        "follow_up_options_reachable",
    )
    for name in new:
        assert name in types and name in _load_suite()["check_definitions"]
    assert next(c for c in case["checks"] if c["type"] == "follow_up_option_count")["max"] == 3


def test_cl02_asks_lk09s_proven_question() -> None:
    suite = _load_suite()
    case = _case(suite, "cl-02")
    lk09 = _case(suite, "lk-09")
    assert case["question"] == lk09["question"]
    assert case["persona"] == lk09["persona"] == "sales"
    assert case["oracle"]["sql"] == "evals/business_query/cross_domain/oracle/lk-09.sql"
    assert case["oracle"]["candidate_ids"] == [701, 704]


def test_ef07_is_not_run_until_v2_has_its_controls() -> None:
    case = _case(_load_suite(), "ef-07")
    assert case["status"] == "not_run"
    assert "Regenerate" in case["not_run_reason"]
