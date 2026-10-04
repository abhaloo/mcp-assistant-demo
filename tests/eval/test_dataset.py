"""The eval-case definition exists ONCE (review M6)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.eval.ask_route.text import normalize_text
from app.eval.dataset import EXCLUDE_PREFIXES, load_eval_cases
from app.paths import REPO_ROOT
from app.rag.access_tiers import get_access_tiers

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

LEGACY_BASENAMES = {
    "salary-records.md",
    "system-access-guide.md",
    "company-handbook.md",
    "hospitality-solutions.md",
    "it-helpdesk-faq.md",
    "services-and-capabilities.md",
    "q2-2024-budget-report.md",
    "supplier-payment-schedule.md",
    "file-handoff-standards.md",
    "press-operating-procedures.md",
    "customer-accounts.md",
    "customer-pricing-guide.md",
    "inventory-procedures.md",
}

LEGACY_TOPIC_PHRASES = [
    "working hours",
    "handbook",
    "payroll",
    "salary",
    "heidelberg",
    "speedmaster",
    "business card",
    "rush order",
    "gold corporate",
    "fire exit",
    "al faris",
]

ALLOWLISTED_TOPIC_IDS = {"fail-06", "fail-11", "tl-forged-cache"}

EVAL_FILES = [
    "tests/eval_dataset.json",
    "evals/prod_ask/cases.jsonl",
    "evals/prod_ask_e2e/suite.json",
    "evals/prod_ask_e2e/run-plan.json",
    "evals/conversation_coordinator/cases.jsonl",
    "evals/tool_layer/document-profile-cases.json",
    "evals/tool_layer/browser-case-map.json",
]


def _all_cases() -> list[dict]:
    return json.loads((REPO_ROOT / "tests" / "eval_dataset.json").read_text(encoding="utf-8"))


def _doc_rows() -> list[dict]:
    """Every case whose source_file ends with .md (excludes non-doc rows and finance-table-*)."""
    return [c for c in _all_cases() if c.get("source_file", "").endswith(".md")]


def _manuals_on_disk() -> list[str]:
    root = REPO_ROOT / "data" / "corpus" / "company"
    return sorted(p.relative_to(REPO_ROOT).as_posix() for p in root.glob("*/manuals/*.md"))


def _manual_text(rel_path: str) -> str:
    return normalize_text((REPO_ROOT / rel_path).read_text(encoding="utf-8"))


def _readable_manuals(role: str, permissions: list[str]) -> list[str]:
    granted_tiers = set(get_access_tiers(role, permissions))
    readable: list[str] = []
    for p in _manuals_on_disk():
        tier_folder = Path(p).parts[3]
        if FOLDER_TO_TIER.get(tier_folder) in granted_tiers:
            readable.append(p)
    return readable


def _cases_from(rel_path: str) -> list[dict]:
    p = REPO_ROOT / rel_path
    if rel_path.endswith(".jsonl"):
        return [
            json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "cases" in data:
        return data["cases"]
    return []


_CASE_TEXT_KEYS = ("question", "title", "expected_behavior_without_access", "answer_oracle")
_ORACLE_TEXT_KEYS = ("notes", "description", "title", "summary")


def _strings_from(case: dict) -> list[str]:
    """Every free-text field a case can carry, across the eval file shapes."""
    candidates = [case.get(key) for key in _CASE_TEXT_KEYS]
    candidates += case.get("question_steps") or []
    candidates.append((case.get("context") or {}).get("question"))
    candidates += [tc.get("notes") for tc in case.get("test_cases") or [] if isinstance(tc, dict)]
    oracle = case.get("oracle")
    if isinstance(oracle, dict):
        candidates += [oracle.get(key) for key in _ORACLE_TEXT_KEYS]
    else:
        candidates.append(oracle)
    return [text for text in candidates if isinstance(text, str)]


def test_semantic_excludes_nonquality_cases():
    cases = load_eval_cases("semantic")
    assert cases, "dataset must load"
    assert all(not c["id"].startswith(EXCLUDE_PREFIXES) for c in cases)
    assert all(c.get("expected_answer") for c in cases)


def test_structured_is_sql_prefixed():
    assert all(c["id"].startswith("sql-") for c in load_eval_cases("structured"))


def test_subset_filter():
    all_sem = load_eval_cases("semantic")
    one = load_eval_cases("semantic", subset=[all_sem[0]["id"]])
    assert [c["id"] for c in one] == [all_sem[0]["id"]]


def test_every_markdown_source_is_a_manual_that_exists():
    cases = _all_cases()
    non_md_with_source = {
        c["id"]
        for c in cases
        if "source_file" in c and not c.get("source_file", "").endswith(".md")
    }
    assert non_md_with_source == {"finance-table-01", "finance-table-02", "finance-table-03"}

    doc_rows = _doc_rows()
    assert doc_rows, "doc rows must not be empty"
    for c in doc_rows:
        source_file = c["source_file"]
        assert MANUAL_RE.match(source_file), (
            f"{c['id']}: source_file '{source_file}' does not match MANUAL_RE"
        )
        assert (REPO_ROOT / source_file).is_file(), (
            f"{c['id']}: source_file '{source_file}' does not exist"
        )


def test_expected_snippet_is_verbatim_in_the_cited_manual():
    for c in _doc_rows():
        raw_text = (REPO_ROOT / c["source_file"]).read_text(encoding="utf-8")
        assert c["expected_snippet"] in raw_text, (
            f"{c['id']}: expected_snippet not found verbatim in {c['source_file']}"
        )


def test_locator_names_a_heading_in_the_cited_manual():
    for c in _doc_rows():
        locator = c["locator"]
        assert locator.startswith("## "), f"{c['id']}: locator '{locator}' must start with '## '"
        raw_text = (REPO_ROOT / c["source_file"]).read_text(encoding="utf-8")
        assert f"\n{locator}\n" in raw_text, (
            f"{c['id']}: heading '\\n{locator}\\n' not found in {c['source_file']}"
        )


def test_question_does_not_copy_a_manual_heading():
    for c in _doc_rows():
        raw_text = (REPO_ROOT / c["source_file"]).read_text(encoding="utf-8")
        headings = [line[3:].strip() for line in raw_text.splitlines() if line.startswith("## ")]
        q_norm = normalize_text(c["question"])
        for h in headings:
            assert q_norm != normalize_text(h), (
                f"{c['id']}: question equals manual heading '{h}' in {c['source_file']}"
            )


def test_key_literals_are_unique_to_the_cited_manual_within_readable_tiers():
    for c in _doc_rows():
        key_literals = c["key_literals"]
        assert isinstance(key_literals, list) and len(key_literals) > 0, (
            f"{c['id']}: key_literals must be a non-empty list"
        )
        cited_path = c["source_file"]
        cited_text_norm = normalize_text((REPO_ROOT / cited_path).read_text(encoding="utf-8"))

        if "test_cases" in c:
            granted = next(tc for tc in c["test_cases"] if tc.get("should_contain_answer"))
            role, permissions = granted["role"], granted["permissions"]
        else:
            role, permissions = c["role"], c["permissions"]

        readable = _readable_manuals(role, permissions)
        allowed_sources = {cited_path} | set(c.get("co_sources", []))

        for literal in key_literals:
            lit_norm = normalize_text(literal)
            assert lit_norm in cited_text_norm, (
                f"{c['id']}: literal '{literal}' not found in cited manual {cited_path}"
            )
            for m_path in readable:
                m_text_norm = _manual_text(m_path)
                if lit_norm in m_text_norm:
                    assert m_path in allowed_sources, (
                        f"{c['id']}: key literal '{literal}' found in readable manual "
                        f"'{m_path}' outside allowed sources {allowed_sources}"
                    )


def test_no_legacy_corpus_file_is_named_in_eval_data():
    for rel_path in EVAL_FILES:
        content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        found = [b for b in LEGACY_BASENAMES if b in content]
        assert not found, f"Legacy corpus basenames found in {rel_path}: {found}"


def test_no_legacy_topic_phrase_in_eval_questions():
    hits: list[str] = []
    for rel_path in EVAL_FILES:
        for c in _cases_from(rel_path):
            case_id = c.get("id") or c.get("case_id")
            if case_id in ALLOWLISTED_TOPIC_IDS:
                continue
            for text in _strings_from(c):
                text_lower = text.casefold()
                hits.extend(
                    f"{rel_path}:{case_id} contains '{phrase}' in '{text}'"
                    for phrase in LEGACY_TOPIC_PHRASES
                    if phrase in text_lower
                )
    assert not hits, f"Legacy topic phrases found ({len(hits)} hits):\n" + "\n".join(hits[:10])
