"""Integrity of the production-ask case set, and proof that its gold bites.

Two jobs here. First, every pinned literal is checked back against the source
of truth it claims to come from — a corpus document or the database answer
key — so a typo in gold cannot silently pass as a model failure. Second, the
mutation tests break the route on purpose and assert the suite notices; a case
whose gold survives a broken feature is not measuring anything.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.eval.ask_route.case import ProdAskCase, load_cases, load_sql_oracle
from app.eval.ask_route.checks import check_accuracy, check_citations
from app.eval.ask_route.markdown import parse_markdown, total_list_items, widest_table
from app.eval.ask_route.scorer import score_observation, transport_parity
from app.eval.ask_route.scorer import write_failures as prod_ask_write_failures
from app.eval.ask_route.text import contains_number, contains_phrase
from app.main import app
from app.policy.manifest_loader import load_manifest
from app.rag.access_tiers import get_access_tiers
from tests.harness.prod_ask_stub_driver import run_stub_json, run_stub_sse

CASES_PATH = Path("evals/prod_ask/cases.jsonl")
ORACLE_PATH = Path("evals/prod_ask/oracle-results.jsonl")

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―"), "-")


@pytest.fixture(scope="module")
def cases() -> dict[str, ProdAskCase]:
    return load_cases(CASES_PATH)


@pytest.fixture(scope="module")
def sql_oracle() -> dict:
    return load_sql_oracle(ORACLE_PATH)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _enable_document_rag_for_stub_transport(monkeypatch):
    """The stub cases exercise semantic JSON/SSE parity, not deployment-off UX."""
    monkeypatch.setattr(settings, "document_rag_enabled", True)


def _provenance_text(raw: str) -> str:
    """Normalized text for tracing a fixture back to its document."""
    folded = unicodedata.normalize("NFKC", raw).translate(_DASHES)
    return " ".join(folded.split()).casefold()


def test_case_file_loads_and_ids_are_unique(cases):
    assert len(cases) >= 12
    assert sorted(cases) == sorted(set(cases))


def test_every_dimension_the_case_claims_has_a_spec(cases):
    for case in cases.values():
        for dimension in case.dimensions:
            assert getattr(case, dimension) is not None, f"{case.id}: {dimension} has no spec"


def test_permission_pairs_are_symmetric(cases):
    for case in cases.values():
        if case.pair_id is None:
            continue
        twin = cases[case.pair_id]
        assert twin.pair_id == case.id, f"{case.id} and {case.pair_id} disagree on pairing"
        assert twin.question == case.question, f"{case.id}: a pair must ask the same question"
        assert twin.principal.permissions != case.principal.permissions


def test_corpus_oracle_files_exist(cases):
    for case in cases.values():
        if case.accuracy is None or case.accuracy.oracle.kind != "corpus":
            continue
        assert Path(case.accuracy.oracle.corpus_file).is_file(), case.id


def test_required_corpus_facts_are_really_in_the_cited_document(cases):
    """Gold must be re-derivable from the document, not from a past answer."""
    for case in cases.values():
        if case.accuracy is None or case.accuracy.oracle.kind != "corpus":
            continue
        document = Path(case.accuracy.oracle.corpus_file).read_text(encoding="utf-8")
        for fact in case.accuracy.required_facts:
            if fact.kind == "number":
                assert contains_number(document, fact.number), f"{case.id}: {fact.label}"
            else:
                assert contains_phrase(document, fact.text), f"{case.id}: {fact.label}"


def test_forbidden_corpus_facts_are_really_in_the_withheld_document(cases):
    """A leak probe is only a probe if the literal exists to be leaked."""
    for case in cases.values():
        if case.accuracy is None or case.accuracy.oracle.kind != "corpus":
            continue
        if not case.accuracy.forbidden_facts:
            continue
        document = Path(case.accuracy.oracle.corpus_file).read_text(encoding="utf-8")
        for fact in case.accuracy.forbidden_facts:
            if fact.kind == "number":
                assert contains_number(document, fact.number), f"{case.id}: {fact.label}"
            else:
                assert contains_phrase(document, fact.text), f"{case.id}: {fact.label}"


def test_forbidden_facts_belong_to_a_tier_the_principal_lacks(cases):
    for case in cases.values():
        if case.accuracy is None or not case.accuracy.forbidden_facts:
            continue
        corpus_file = case.accuracy.oracle.corpus_file
        if corpus_file is None:
            continue
        tier = Path(corpus_file).parent.name.replace("-", " ")
        assert tier not in {t.casefold() for t in case.expected_access_tiers}, case.id


def test_stub_fixture_chunks_are_traceable_to_their_document(cases):
    for case in cases.values():
        if case.stub is None:
            continue
        for chunk in case.stub.docs:
            document = _provenance_text(Path(chunk.source_file).read_text(encoding="utf-8"))
            for line in chunk.content.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                assert _provenance_text(stripped) in document, (
                    f"{case.id}: fixture line not found in {chunk.source_file}: {stripped!r}"
                )


def test_stub_fixture_tier_matches_its_corpus_folder(cases):
    for case in cases.values():
        if case.stub is None:
            continue
        for chunk in case.stub.docs:
            folder = Path(chunk.source_file).parent.name.replace("-", " ")
            assert chunk.access_tier == folder, f"{case.id}: {chunk.source_file}"


def test_expected_tiers_agree_with_the_permission_policy(cases):
    """Drift tripwire, not the oracle.

    The expected tiers are written by hand from the documented policy. This
    test says the hand derivation and the shipped mapping still agree; when it
    fails, one of the two changed and a human decides which.
    """
    for case in cases.values():
        resolved = sorted(get_access_tiers(case.principal.role, case.principal.permissions))
        assert resolved == sorted(case.expected_access_tiers), case.id


def test_sql_oracle_rows_exist_for_every_structured_case(cases, sql_oracle):
    for case in cases.values():
        if case.accuracy is None or case.accuracy.oracle.kind != "sql":
            continue
        assert case.accuracy.oracle.sql_case_id in sql_oracle, case.id


def test_scalar_required_facts_equal_the_database_answer_key(cases, sql_oracle):
    for case in cases.values():
        if case.accuracy is None or case.accuracy.oracle.kind != "sql":
            continue
        row = sql_oracle[case.accuracy.oracle.sql_case_id]
        if row.scalar is None:
            continue
        for fact in case.accuracy.required_facts:
            if fact.kind == "number":
                assert abs(float(row.scalar) - fact.number) <= 0.01, case.id


def test_currency_amounts_come_from_the_answer_key(cases, sql_oracle):
    for case in cases.values():
        if case.rich_text is None or not case.rich_text.oracle_amounts:
            continue
        if case.accuracy is None or case.accuracy.oracle.kind != "sql":
            continue
        row = sql_oracle[case.accuracy.oracle.sql_case_id]
        numeric = {
            float(value)
            for record in row.rows
            for value in record
            if isinstance(value, int | float) and not isinstance(value, bool)
        }
        for amount in case.rich_text.oracle_amounts:
            assert any(abs(amount - value) <= 0.01 for value in numeric), f"{case.id}: {amount}"


def test_question_resources_are_declared_by_the_manifest(cases):
    declared = set(load_manifest().resources)
    for case in cases.values():
        if case.follow_ups is None:
            continue
        unknown = set(case.follow_ups.question_resources) - declared
        assert not unknown, f"{case.id}: undeclared resources {sorted(unknown)}"


def test_structured_cases_never_claim_stub_mode(cases):
    for case in cases.values():
        if case.route == "structured":
            assert "stub" not in case.modes, f"{case.id}: the stub harness has no SQL path"


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------


def test_table_is_parsed_with_its_shape():
    document = parse_markdown("| A | B | C |\n| --- | --- | --- |\n| 1 | 2 | 3 |\n| 4 | 5 | 6 |\n")
    table = widest_table(document)
    assert document.well_formed
    assert table is not None
    assert table.column_count == 3
    assert len(table.body_rows) == 2


def test_fenced_code_is_not_scanned_for_defects():
    document = parse_markdown("```\n<div>not html to the reader</div>\nhttps://x.test\n```\n")
    assert document.well_formed


def test_list_items_are_counted_across_blocks():
    document = parse_markdown("- one\n- two\n\ntext\n\n1. three\n2. four\n")
    assert total_list_items(document) == 4


# ---------------------------------------------------------------------------
# Mutation tests: break the route, prove the suite notices
# ---------------------------------------------------------------------------


def test_over_granting_permissions_makes_the_tier_case_fail(cases, client):
    """pa-02 withholds pricing from a store principal. Grant sales and it leaks."""
    case = cases["pa-02"]
    clean = run_stub_json(client, case)
    assert result(check_accuracy(case, clean, None), "accuracy.no_out_of_tier_source_retrieved")

    widened = case.model_copy(
        update={
            "principal": case.principal.model_copy(
                update={"permissions": [*case.principal.permissions, "view quotation"]}
            )
        }
    )
    leaked = run_stub_json(client, widened)
    outcomes = check_accuracy(case, leaked, None)
    check = next(o for o in outcomes if o.check_id == "accuracy.no_out_of_tier_source_retrieved")
    assert not check.passed, "a widened permission set must trip the retrieval boundary check"
    assert "sales" in check.detail


def test_binding_a_marker_to_the_wrong_document_fails_the_allowlist(cases, client):
    """pa-08 cites the second fixture chunk. Swap the order and the claim moves."""
    case = cases["pa-08"]
    clean = run_stub_json(client, case)
    assert result(check_citations(case, clean), "citations.cited_files_contain_the_claim")

    swapped = case.model_copy(
        update={"stub": case.stub.model_copy(update={"docs": list(reversed(case.stub.docs))})}
    )
    wrong = run_stub_json(client, swapped)
    outcomes = check_citations(case, wrong)
    check = next(o for o in outcomes if o.check_id == "citations.cited_files_contain_the_claim")
    assert not check.passed
    assert "company-handbook.md" in check.detail


def test_an_unstripped_marker_would_fail_the_citation_hygiene_check(cases, client):
    """pa-09 relies on the route stripping an out-of-range marker before the wire."""
    case = cases["pa-09"]
    observation = run_stub_json(client, case)
    assert "[Source 9]" not in observation.answer.answer
    assert result(check_citations(case, observation), "citations.no_unbound_markers_in_body")

    regressed = observation.model_copy(
        update={"answer": observation.answer.model_copy(update={"answer": case.stub.model_answer})}
    )
    outcomes = check_citations(case, regressed)
    check = next(o for o in outcomes if o.check_id == "citations.no_unbound_markers_in_body")
    assert not check.passed
    assert "[9]" in check.detail


def test_stripping_the_bad_marker_leaves_the_table_intact(cases, client):
    """The repair must not damage the block the panel renders."""
    case = cases["pa-09"]
    observation = run_stub_json(client, case)
    document = parse_markdown(observation.answer.answer)
    table = widest_table(document)
    assert document.well_formed, document.defects
    assert table is not None
    assert len(table.body_rows) == 3


@pytest.mark.parametrize("case_id", ["pa-01", "pa-03", "pa-08", "pa-09", "pa-12"])
def test_json_and_sse_deliver_the_same_payload(cases, client, case_id):
    case = cases[case_id]
    parity = transport_parity(run_stub_json(client, case), run_stub_sse(client, case))
    assert parity.passed, parity.detail


def result(outcomes, check_id: str) -> bool:
    match = next((o for o in outcomes if o.check_id == check_id), None)
    assert match is not None, f"{check_id} did not run"
    return getattr(match, "passed", False)


def test_a_failing_case_writes_a_g4_file_with_the_canonical_name(cases, client, tmp_path):
    case = cases["pa-09"]
    observation = run_stub_json(client, case)
    regressed = observation.model_copy(
        update={"answer": observation.answer.model_copy(update={"answer": case.stub.model_answer})}
    )
    score = score_observation(case, regressed, manifest_resources=frozenset())
    assert score.passed is False

    written = prod_ask_write_failures(cases, [score], out_dir=tmp_path, run_id="pa-test-run")
    assert len(written) == 1
    assert written[0].name == "pa-test-run_pa-09_stub-json.json"
    record = json.loads(written[0].read_text(encoding="utf-8"))
    assert record["suite"] == "prod_ask"
    assert record["case_id"] == "pa-09"
    assert record["pass_rate"] < 1.0
    assert any(
        f["check_id"] == "citations.no_unbound_markers_in_body" for f in record["failed_checks"]
    )


def test_a_passing_case_writes_no_g4_file(cases, client, tmp_path):
    case = cases["pa-09"]
    score = score_observation(case, run_stub_json(client, case), manifest_resources=frozenset())
    assert prod_ask_write_failures(cases, [score], out_dir=tmp_path, run_id="pa-test-run") == []


def test_no_case_id_or_gold_literal_lives_in_a_production_module():
    """ADR 0047 invariant 12, enforced rather than trusted."""
    case_id = re.compile(r"\bpa-\d{2}\b")
    offenders: list[str] = []
    for path in Path("app").rglob("*.py"):
        if path.parts[1] == "eval":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if case_id.search(text):
            offenders.append(str(path))
    assert not offenders, f"production modules reference eval case ids: {offenders}"
