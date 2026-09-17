"""Deterministic quality checks over one production ask response.

Every check here costs nothing to run and states a property that either holds
or does not. A check that needs a model verdict does not belong in this module
— see ``app/eval/ask_route/judge.py``.

Two rules govern the code below:

* A check compares the answer to an oracle the ask route did not produce — a
  corpus literal, a database answer key, the signed policy manifest, or the
  markdown grammar itself.
* A check that cannot be evaluated on the evidence available says so. It
  returns a skip with a reason, or runs degraded and says which weaker
  evidence it used. It never silently passes.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.eval.ask_route.case import (
    AskObservation,
    CitationSpec,
    Dimension,
    FollowUpSpec,
    LiteralFact,
    ProdAskCase,
    RichTextSpec,
    RunMode,
    SqlOracleRow,
)
from app.eval.ask_route.markdown import parse_markdown, total_list_items, widest_table
from app.eval.ask_route.text import contains_number, contains_phrase, currency_windows
from app.models.schemas import Answer

# The wire contract for a citation marker, restated here on purpose. Importing
# the production pattern would make the check agree with the parser by
# construction instead of testing it.
_MARKER_RE = re.compile(r"\[\s*Source\s+(\d+)\s*\]", re.IGNORECASE)

_FOLLOW_UP_MIN_LEN = 2
_FOLLOW_UP_MAX_LEN = 160


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    dimension: Dimension
    passed: bool
    detail: str
    degraded: bool = False


@dataclass(frozen=True)
class SkippedCheck:
    check_id: str
    dimension: Dimension
    reason: str


CheckOutcome = CheckResult | SkippedCheck


def _ok(check_id: str, dimension: Dimension, detail: str = "") -> CheckResult:
    return CheckResult(check_id=check_id, dimension=dimension, passed=True, detail=detail)


def _fail(check_id: str, dimension: Dimension, detail: str) -> CheckResult:
    return CheckResult(check_id=check_id, dimension=dimension, passed=False, detail=detail)


def _fact_present(answer_text: str, fact: LiteralFact) -> bool:
    if fact.kind == "number":
        assert fact.number is not None
        if contains_number(answer_text, fact.number):
            return True
        return any(contains_phrase(answer_text, alias) for alias in fact.aliases)
    candidates = [fact.text or "", *fact.aliases]
    return any(contains_phrase(answer_text, candidate) for candidate in candidates if candidate)


# ---------------------------------------------------------------------------
# Accuracy
# ---------------------------------------------------------------------------


def check_accuracy(
    case: ProdAskCase,
    observation: AskObservation,
    sql_oracle: SqlOracleRow | None,
) -> list[CheckOutcome]:
    """Literal-level truth checks against the corpus or the database answer key."""
    spec = case.accuracy
    if spec is None:
        return [SkippedCheck("accuracy.required_facts_present", "accuracy", "no accuracy spec")]

    body = observation.answer.answer
    outcomes: list[CheckOutcome] = []

    if observation.mode == "stub":
        outcomes.append(
            SkippedCheck(
                "accuracy.required_facts_present",
                "accuracy",
                "stub mode scripts the model output, so a required fact would be self-fulfilling",
            )
        )
    elif not spec.required_facts:
        outcomes.append(
            SkippedCheck("accuracy.required_facts_present", "accuracy", "no required facts pinned")
        )
    else:
        missing = [fact.label for fact in spec.required_facts if not _fact_present(body, fact)]
        outcomes.append(
            _fail(
                "accuracy.required_facts_present",
                "accuracy",
                f"missing oracle facts: {missing}",
            )
            if missing
            else _ok(
                "accuracy.required_facts_present",
                "accuracy",
                f"{len(spec.required_facts)} oracle fact(s) present",
            )
        )

    outcomes.append(_check_forbidden_facts(case, observation))
    outcomes.append(_check_retrieved_tiers(case, observation))
    outcomes.append(_check_sql_oracle(case, observation, sql_oracle))
    outcomes.append(_check_granted_tiers(case, observation))
    return outcomes


def _check_forbidden_facts(case: ProdAskCase, observation: AskObservation) -> CheckOutcome:
    """Literals from tiers this principal cannot see must not reach the answer."""
    check_id = "accuracy.forbidden_facts_absent"
    spec = case.accuracy
    if spec is None or not spec.forbidden_facts:
        return SkippedCheck(check_id, "accuracy", "no forbidden facts pinned")
    if observation.mode == "stub":
        # The stub scripts the answer text, so its silence about a forbidden
        # literal is the fixture's doing, not the route's. The retrieval-level
        # check below is the one that carries weight in stub mode.
        return SkippedCheck(
            check_id, "accuracy", "stub mode scripts the answer text, so absence proves nothing"
        )
    body = observation.answer.answer
    leaked = [fact.label for fact in spec.forbidden_facts if _fact_present(body, fact)]
    if leaked:
        return _fail(check_id, "accuracy", f"answer discloses out-of-tier facts: {leaked}")
    return _ok(check_id, "accuracy", f"{len(spec.forbidden_facts)} out-of-tier fact(s) absent")


def _check_retrieved_tiers(case: ProdAskCase, observation: AskObservation) -> CheckOutcome:
    """Nothing outside the granted tiers may be retrieved at all.

    This is the pre-retrieval security boundary, so it is checked on the ids
    the retriever returned rather than on the prose the model wrote. The tier
    of a corpus chunk is its folder, which is how ingestion assigns the tier.
    """
    check_id = "accuracy.no_out_of_tier_source_retrieved"
    if observation.retrieved_source_ids is None:
        return SkippedCheck(check_id, "accuracy", "run did not record the retrieved id set")
    if not case.expected_access_tiers:
        return SkippedCheck(check_id, "accuracy", "case pins no expected tiers")
    allowed = {tier.casefold() for tier in case.expected_access_tiers}
    offenders: list[str] = []
    for source_id in observation.retrieved_source_ids:
        tier = corpus_tier_of(source_id)
        if tier is not None and tier not in allowed:
            offenders.append(f"{source_id} (tier {tier})")
    if offenders:
        return _fail(check_id, "accuracy", f"retrieved out-of-tier chunks: {offenders}")
    return _ok(
        check_id,
        "accuracy",
        f"{len(observation.retrieved_source_ids)} retrieved chunk(s) all within {sorted(allowed)}",
    )


def corpus_tier_of(source_id: str) -> str | None:
    """Tier folder of a corpus chunk id, or None when the id is not a corpus path.

    Ingestion reads the tier from the folder under ``corpus/company``; the
    folder for a two-word tier uses a hyphen where the tier name uses a space.
    """
    parts = source_id.replace("\\", "/").split("/")
    if "company" not in parts:
        return None
    index = parts.index("company") + 1
    if index >= len(parts) - 1:
        return None
    return parts[index].replace("-", " ").casefold()


def _check_granted_tiers(case: ProdAskCase, observation: AskObservation) -> CheckOutcome:
    """The tiers the request resolved to must equal the tiers the policy grants.

    ``expected_access_tiers`` is written by hand from the documented permission
    policy, not read from the mapping table, so an accidental widening of that
    table fails here instead of agreeing with itself.
    """
    check_id = "accuracy.granted_tiers_match_policy"
    if not case.expected_access_tiers:
        return SkippedCheck(check_id, "accuracy", "case pins no expected tiers")
    if observation.granted_access_tiers is None:
        return SkippedCheck(check_id, "accuracy", "run did not record the granted tiers")
    expected = sorted(case.expected_access_tiers)
    actual = sorted(observation.granted_access_tiers)
    if expected != actual:
        return _fail(check_id, "accuracy", f"expected tiers {expected}, request resolved {actual}")
    return _ok(check_id, "accuracy", f"tiers {actual}")


def _check_sql_oracle(
    case: ProdAskCase,
    observation: AskObservation,
    sql_oracle: SqlOracleRow | None,
) -> CheckOutcome:
    check_id = "accuracy.sql_oracle_values_present"
    spec = case.accuracy
    if spec is None or spec.oracle.kind != "sql":
        return SkippedCheck(check_id, "accuracy", "case has no SQL oracle")
    if observation.mode == "stub":
        return SkippedCheck(check_id, "accuracy", "stub mode does not run the structured route")
    if sql_oracle is None:
        return SkippedCheck(
            check_id, "accuracy", f"oracle row {spec.oracle.sql_case_id} not loaded"
        )

    body = observation.answer.answer
    missing: list[str] = []
    for row in sql_oracle.rows:
        for column, value in zip(sql_oracle.columns, row, strict=True):
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, int | float):
                if not contains_number(body, float(value)):
                    missing.append(f"{column}={value}")
            elif isinstance(value, str) and not contains_phrase(body, value.strip()):
                missing.append(f"{column}={value.strip()!r}")
    if missing:
        return _fail(check_id, "accuracy", f"answer key values absent from answer: {missing[:8]}")
    return _ok(check_id, "accuracy", f"all {sql_oracle.row_count} answer-key row(s) rendered")


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def _body_markers(answer_text: str) -> list[int]:
    return [int(match.group(1)) for match in _MARKER_RE.finditer(answer_text)]


def check_citations(case: ProdAskCase, observation: AskObservation) -> list[CheckOutcome]:
    spec = case.citations
    if spec is None:
        return [SkippedCheck("citations.parsed_matches_expectation", "citations", "no spec")]

    answer = observation.answer
    body_markers = _body_markers(answer.answer)
    cited = answer.citations.cited
    outcomes: list[CheckOutcome] = [
        _parsed_matches(spec, answer),
        _cited_count_within_bounds(spec, answer),
        _markers_present_in_body(body_markers, cited),
        _no_orphan_markers(answer, body_markers, cited),
        _cited_ids_in_retrieved_set(observation),
        _cited_files_support_claim(spec, answer),
    ]
    return outcomes


def _parsed_matches(spec: CitationSpec, answer: Answer) -> CheckResult:
    actual = answer.citations.parsed
    if actual == spec.expect_parsed:
        return _ok("citations.parsed_matches_expectation", "citations", f"parsed={actual}")
    return _fail(
        "citations.parsed_matches_expectation",
        "citations",
        f"expected parsed={spec.expect_parsed}, got {actual}",
    )


def _cited_count_within_bounds(spec: CitationSpec, answer: Answer) -> CheckResult:
    count = len(answer.citations.cited)
    if count < spec.min_cited:
        return _fail(
            "citations.count_within_bounds",
            "citations",
            f"{count} citation(s), case requires at least {spec.min_cited}",
        )
    if spec.max_cited is not None and count > spec.max_cited:
        return _fail(
            "citations.count_within_bounds",
            "citations",
            f"{count} citation(s), case allows at most {spec.max_cited}",
        )
    return _ok("citations.count_within_bounds", "citations", f"{count} citation(s)")


def _markers_present_in_body(body_markers: list[int], cited: Iterable) -> CheckResult:
    present = set(body_markers)
    orphaned = sorted({ref.marker for ref in cited} - present)
    if orphaned:
        return _fail(
            "citations.payload_markers_appear_in_body",
            "citations",
            f"citations payload claims markers the answer text never shows: {orphaned}",
        )
    return _ok("citations.payload_markers_appear_in_body", "citations", "payload matches body")


def _no_orphan_markers(answer: Answer, body_markers: list[int], cited: Iterable) -> CheckResult:
    bound = {ref.marker for ref in cited}
    unbound = sorted(set(body_markers) - bound)
    if not answer.citations.parsed and body_markers:
        return _fail(
            "citations.no_unbound_markers_in_body",
            "citations",
            f"answer shows markers {sorted(set(body_markers))} while citations.parsed is false",
        )
    if unbound:
        return _fail(
            "citations.no_unbound_markers_in_body",
            "citations",
            f"answer shows markers with no binding: {unbound}",
        )
    return _ok("citations.no_unbound_markers_in_body", "citations", "no unbound marker on the wire")


def _cited_ids_in_retrieved_set(observation: AskObservation) -> CheckResult:
    answer = observation.answer
    cited_ids = {ref.id for ref in answer.citations.cited}
    check_id = "citations.cited_ids_were_retrieved"
    if observation.retrieved_source_ids is not None:
        unknown = sorted(cited_ids - set(observation.retrieved_source_ids))
        if unknown:
            return _fail(
                check_id,
                "citations",
                f"cited sources that retrieval never returned: {unknown}",
            )
        return _ok(check_id, "citations", f"{len(cited_ids)} cited id(s) all retrieved")

    # Without the pre-filter retrieval set the strongest available statement is
    # that the payload agrees with the sources shipped beside it. Answer.sources
    # is already the cited subset on the semantic route, so this is weaker than
    # the check the case asks for and is reported as degraded.
    shipped = {source.id for source in answer.sources if source.id}
    unknown = sorted(cited_ids - shipped)
    detail = (
        f"cited ids absent from answer.sources: {unknown}"
        if unknown
        else f"{len(cited_ids)} cited id(s) present in answer.sources"
    )
    return CheckResult(
        check_id=check_id,
        dimension="citations",
        passed=not unknown,
        detail=f"{detail} (no pre-filter retrieval set recorded)",
        degraded=True,
    )


def _cited_files_support_claim(spec: CitationSpec, answer: Answer) -> CheckOutcome:
    check_id = "citations.cited_files_contain_the_claim"
    if not spec.supporting_source_files:
        return SkippedCheck(check_id, "citations", "no supporting-source allowlist pinned")
    allowed = {name.replace("\\", "/").rsplit("/", 1)[-1] for name in spec.supporting_source_files}
    cited_ids = {ref.id for ref in answer.citations.cited}
    offenders: list[str] = []
    for source in answer.sources:
        if source.id not in cited_ids:
            continue
        basename = source.source_file.replace("\\", "/").rsplit("/", 1)[-1]
        if basename not in allowed:
            offenders.append(basename)
    if offenders:
        return _fail(
            check_id,
            "citations",
            f"cited documents that do not contain the claim: {sorted(set(offenders))}",
        )
    return _ok(check_id, "citations", f"cited documents within allowlist {sorted(allowed)}")


# ---------------------------------------------------------------------------
# Rich text
# ---------------------------------------------------------------------------


def check_rich_text(case: ProdAskCase, observation: AskObservation) -> list[CheckOutcome]:
    spec = case.rich_text
    if spec is None:
        return [SkippedCheck("rich_text.markdown_well_formed", "rich_text", "no spec")]

    document = parse_markdown(observation.answer.answer)
    outcomes: list[CheckOutcome] = [
        _fail("rich_text.markdown_well_formed", "rich_text", "; ".join(document.defects))
        if document.defects
        else _ok("rich_text.markdown_well_formed", "rich_text", "no structural defect")
    ]
    outcomes.append(_structure_matches(spec, document, observation.mode))
    outcomes.append(_currency_rendering(spec, observation.answer.answer, observation.mode))
    return outcomes


def _structure_matches(spec: RichTextSpec, document, mode: RunMode) -> CheckOutcome:
    check_id = "rich_text.structure_matches_result_shape"
    if mode == "stub":
        return SkippedCheck(
            check_id,
            "rich_text",
            "stub mode scripts the answer body, so its shape proves nothing about the model",
        )
    wanted = spec.structure
    if wanted.kind == "prose":
        return _ok(check_id, "rich_text", "prose answer, no block requirement")
    if wanted.kind == "table":
        table = widest_table(document)
        if table is None:
            return _fail(
                check_id, "rich_text", "result has several rows but the answer has no table"
            )
        if len(table.body_rows) < wanted.min_body_rows:
            return _fail(
                check_id,
                "rich_text",
                f"table has {len(table.body_rows)} body rows, result needs {wanted.min_body_rows}",
            )
        if table.column_count < wanted.min_columns:
            return _fail(
                check_id,
                "rich_text",
                f"table has {table.column_count} columns, result needs {wanted.min_columns}",
            )
        return _ok(check_id, "rich_text", f"table {table.column_count}x{len(table.body_rows)}")
    items = total_list_items(document)
    if items < wanted.min_items:
        return _fail(
            check_id,
            "rich_text",
            f"answer has {items} list item(s), result needs {wanted.min_items}",
        )
    return _ok(check_id, "rich_text", f"{items} list item(s)")


def _currency_rendering(spec: RichTextSpec, body: str, mode: RunMode) -> CheckOutcome:
    check_id = "rich_text.currency_rendering"
    if mode == "stub":
        return SkippedCheck(check_id, "rich_text", "stub mode scripts the answer body")
    banned = [symbol for symbol in spec.forbidden_currency_symbols if symbol in body]
    if banned:
        return _fail(check_id, "rich_text", f"answer uses a foreign currency symbol: {banned}")
    if not spec.oracle_amounts or not spec.currency_tokens:
        return SkippedCheck(check_id, "rich_text", "no money amounts pinned for this case")
    tokens = [token.casefold() for token in spec.currency_tokens]
    unlabelled: list[float] = []
    for amount in spec.oracle_amounts:
        windows = [window.casefold() for window in currency_windows(body, amount)]
        if not windows:
            # Absence is an accuracy failure, reported by the accuracy checks.
            continue
        if not any(token in window for window in windows for token in tokens):
            unlabelled.append(amount)
    if unlabelled:
        return _fail(
            check_id,
            "rich_text",
            f"money amounts rendered with no currency label: {unlabelled}",
        )
    return _ok(
        check_id, "rich_text", f"currency labels present for {len(spec.oracle_amounts)} amount(s)"
    )


# ---------------------------------------------------------------------------
# Follow-up suggestions
# ---------------------------------------------------------------------------


def check_follow_ups(
    case: ProdAskCase,
    observation: AskObservation,
    manifest_resources: frozenset[str],
) -> list[CheckOutcome]:
    spec = case.follow_ups
    if spec is None:
        return [SkippedCheck("follow_ups.bounded", "follow_ups", "no spec")]

    suggestions = observation.answer.follow_up_suggestions
    return [
        _follow_ups_bounded(spec, suggestions),
        _follow_ups_presence(spec, suggestions),
        _follow_ups_resource_containment(case, spec, suggestions, manifest_resources),
        _follow_ups_specific(spec, suggestions),
        _follow_ups_resources_declared(suggestions, manifest_resources),
    ]


def _label_variants(resource: str) -> tuple[str, ...]:
    """Singular and plural surface forms of one manifest resource name.

    The vocabulary is closed — it is whatever the manifest declares — so this
    is ordinary English morphology over a fixed word list, not a general
    pluralizer and not a guess at how a model phrases things.
    """
    label = resource.replace("_", " ")
    if label.endswith("y") and not label.endswith(("ay", "ey", "iy", "oy", "uy")):
        plural = f"{label[:-1]}ies"
    elif label.endswith(("s", "x", "z", "ch", "sh")):
        plural = f"{label}es"
    else:
        plural = f"{label}s"
    return (label, plural)


def _mentioned_resources(text: str, manifest_resources: frozenset[str]) -> set[str]:
    """Manifest resource names the text names, matched on word boundaries.

    Resource names are the manifest's own closed vocabulary, so this is a
    lookup against an independent artifact rather than a guess at phrasing.
    """
    found: set[str] = set()
    for resource in manifest_resources:
        pattern = "|".join(re.escape(form) for form in _label_variants(resource))
        if re.search(rf"\b(?:{pattern})\b", text, re.IGNORECASE):
            found.add(resource)
    return found


def _follow_ups_bounded(spec: FollowUpSpec, suggestions: list[str]) -> CheckResult:
    check_id = "follow_ups.bounded"
    if len(suggestions) > spec.max_suggestions:
        return _fail(
            check_id, "follow_ups", f"{len(suggestions)} suggestions, max {spec.max_suggestions}"
        )
    seen: set[str] = set()
    for suggestion in suggestions:
        if not _FOLLOW_UP_MIN_LEN <= len(suggestion) <= _FOLLOW_UP_MAX_LEN:
            return _fail(check_id, "follow_ups", f"suggestion length out of range: {suggestion!r}")
        if " ".join(suggestion.split()) != suggestion:
            return _fail(
                check_id, "follow_ups", f"suggestion is not whitespace-normalized: {suggestion!r}"
            )
        key = suggestion.casefold()
        if key in seen:
            return _fail(check_id, "follow_ups", f"duplicate suggestion: {suggestion!r}")
        seen.add(key)
    return _ok(check_id, "follow_ups", f"{len(suggestions)} bounded, unique suggestion(s)")


def _follow_ups_presence(spec: FollowUpSpec, suggestions: list[str]) -> CheckResult:
    check_id = "follow_ups.presence_matches_expectation"
    if bool(suggestions) == spec.expect_any:
        return _ok(check_id, "follow_ups", f"{len(suggestions)} suggestion(s), as expected")
    return _fail(
        check_id,
        "follow_ups",
        f"expected suggestions={spec.expect_any}, got {len(suggestions)}",
    )


def _follow_ups_resource_containment(
    case: ProdAskCase,
    spec: FollowUpSpec,
    suggestions: list[str],
    manifest_resources: frozenset[str],
) -> CheckResult:
    """A chip must not introduce a resource the user's own question never named."""
    check_id = "follow_ups.no_new_resource_disclosed"
    asked = _mentioned_resources(case.question, manifest_resources) | set(spec.question_resources)
    offenders: dict[str, list[str]] = {}
    for suggestion in suggestions:
        extra = sorted(_mentioned_resources(suggestion, manifest_resources) - asked)
        if extra:
            offenders[suggestion] = extra
    if offenders:
        return _fail(check_id, "follow_ups", f"chips name unasked resources: {offenders}")
    return _ok(check_id, "follow_ups", f"chips stay within {sorted(asked) or 'no named resource'}")


def _follow_ups_specific(spec: FollowUpSpec, suggestions: list[str]) -> CheckOutcome:
    check_id = "follow_ups.specific_to_the_question"
    if not spec.question_resources:
        return SkippedCheck(check_id, "follow_ups", "question names no manifest resource")
    if not suggestions:
        return SkippedCheck(check_id, "follow_ups", "no suggestions to judge")
    wanted = frozenset(spec.question_resources)
    generic = [s for s in suggestions if not _mentioned_resources(s, wanted)]
    if generic:
        return _fail(
            check_id,
            "follow_ups",
            f"chips that name none of {sorted(wanted)}: {generic}",
        )
    return _ok(check_id, "follow_ups", f"every chip names one of {sorted(wanted)}")


def _follow_ups_resources_declared(
    suggestions: list[str], manifest_resources: frozenset[str]
) -> CheckOutcome:
    check_id = "follow_ups.resources_declared_by_manifest"
    if not suggestions:
        return SkippedCheck(check_id, "follow_ups", "no suggestions to judge")
    undeclared = [s for s in suggestions if not _mentioned_resources(s, manifest_resources)]
    if undeclared:
        return _fail(
            check_id,
            "follow_ups",
            f"chips that name no manifest-declared resource: {undeclared}",
        )
    return _ok(check_id, "follow_ups", "every chip names a manifest-declared resource")


def check_permission_variance(
    case: ProdAskCase,
    observation: AskObservation,
    twin: AskObservation,
) -> CheckOutcome:
    """Compare a case with its permission twin on suggestions and disclosure."""
    check_id = "follow_ups.varies_with_permissions"
    spec = case.follow_ups
    if spec is None:
        return SkippedCheck(check_id, "follow_ups", "no follow-up spec")
    mine = [s.casefold() for s in observation.answer.follow_up_suggestions]
    theirs = [s.casefold() for s in twin.answer.follow_up_suggestions]
    differs = mine != theirs
    if differs == spec.expected_varies_by_permission:
        return _ok(
            check_id,
            "follow_ups",
            f"suggestion sets differ={differs}, matching the recorded contract",
        )
    return _fail(
        check_id,
        "follow_ups",
        f"expected differ={spec.expected_varies_by_permission} across the permission pair, "
        f"got differ={differs}",
    )
