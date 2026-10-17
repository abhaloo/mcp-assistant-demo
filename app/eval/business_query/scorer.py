"""Score a Business Query outcome against the hand-authored answer key.

Two judgements per case, kept separate because they fail for different reasons:

* **outcome** — did the module do the right *kind* of thing (answer, ask, refuse,
  deny)? Five of the twenty cases are scored on this alone: refusing honestly has
  no numeric answer to check.
* **literal** — for cases that should return data, do the values match the oracle?

Gate scoring uses a frozen oracle-column to result-member map. Strings are trimmed
and case-folded because real customer names carry stray whitespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.business_query.seal.events import BusinessQueryExecutionEvent

# Oracle values are rounded to 2dp; the module may carry more precision through
# the same arithmetic. Anything inside a cent is the same money.
_MONEY_TOLERANCE = 0.01
_CURRENCY_QUANTUM = Decimal("0.01")

# The key round-trips through JSON so a timestamp arrives as a string, while the
# module returns a live datetime. Both sides collapse to one spelling: lowercase
# 't' separator, seconds precision.
_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})[ t](\d{2}:\d{2}:\d{2})(?:\.\d+)?$")


def _canonical_text(raw: str) -> str:
    text = raw.strip().casefold()
    stamp = _TIMESTAMP.match(text)
    return f"{stamp.group(1)}t{stamp.group(2)}" if stamp else text


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    outcome_expected: str
    outcome_actual: str
    outcome_match: bool
    literal_match: bool | None
    passed: bool
    detail: str


class ValueKind(StrEnum):
    CURRENCY = "currency"
    NON_MONEY = "non_money"


@dataclass(frozen=True)
class MemberMapping:
    """One oracle column and the result member(s) that may carry it.

    A question can have more than one correct route: "the order number for job
    24692" is answered by job.customer_order_number and by
    customer_order.order_number alike. Naming only one grades the route rather
    than the answer, so a case whose planner legitimately picks either becomes
    a coin flip. Alternatives must be declared in the frozen spec — they widen
    what counts as the right MEMBER, never what counts as the right VALUE.
    """

    oracle_column: str
    result_member: str
    value_kind: ValueKind
    result_member_alternatives: tuple[str, ...] = ()

    @property
    def accepted_members(self) -> tuple[str, ...]:
        return (self.result_member, *self.result_member_alternatives)

    def resolve_member(self, schema: set[str]) -> str | None:
        """The declared route this result actually used, in declared order."""
        return next((name for name in self.accepted_members if name in schema), None)


@dataclass(frozen=True)
class CaseScoringSpec:
    member_mappings: tuple[MemberMapping, ...]
    row_key: tuple[str, ...]
    expected_row_count: int
    expected_total_row_count: int
    expected_truncated: bool
    ordered: bool = False
    metadata_allowlist: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        oracle_columns = [mapping.oracle_column for mapping in self.member_mappings]
        # Every accepted route, not just the primary: two mappings that share
        # an alternative would make the resolved member ambiguous.
        result_members = [
            name for mapping in self.member_mappings for name in mapping.accepted_members
        ]
        if not self.member_mappings or any(not name for name in oracle_columns + result_members):
            raise ValueError("scoring spec requires named member mappings")
        if len(set(oracle_columns)) != len(oracle_columns):
            raise ValueError("scoring spec oracle mappings must be unique")
        if len(set(result_members)) != len(result_members):
            raise ValueError("scoring spec result mappings must be unique")
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in (self.expected_row_count, self.expected_total_row_count)
        ):
            raise ValueError("scoring spec row counts must be non-negative integers")
        if self.expected_total_row_count < self.expected_row_count:
            raise ValueError("scoring spec total count cannot be below returned count")
        if self.expected_truncated != (self.expected_total_row_count > self.expected_row_count):
            raise ValueError("scoring spec truncation must match its frozen counts")
        if len(set(self.row_key)) != len(self.row_key) or not set(self.row_key).issubset(
            oracle_columns
        ):
            raise ValueError("scoring spec row key must use unique oracle columns")
        if self.expected_row_count > 1 and not self.row_key:
            raise ValueError("multi-row scoring spec requires a row key")
        if set(result_members) & self.metadata_allowlist:
            raise ValueError("metadata allowlist cannot repeat mapped result members")


def _normalize(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, datetime | date):
        return _canonical_text(value.isoformat())
    if value is None:
        return None
    return _canonical_text(str(value))


def _cells_equal(expected: object, actual: object) -> bool:
    expected, actual = _normalize(expected), _normalize(actual)
    if isinstance(expected, float) and isinstance(actual, float):
        return abs(expected - actual) <= _MONEY_TOLERANCE
    return expected == actual


def _typed_cells_equal(expected: object, actual: object, value_kind: ValueKind) -> bool:
    try:
        return _normalize_typed(expected, value_kind) == _normalize_typed(actual, value_kind)
    except (ValueError, ArithmeticError):
        return False


def _normalize_typed(value: object, value_kind: ValueKind) -> object:
    if value_kind is ValueKind.CURRENCY:
        return Decimal(str(value)).quantize(_CURRENCY_QUANTUM, rounding=ROUND_HALF_UP)
    return _normalize_non_money(value)


def _normalize_non_money(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal | int | float):
        return Decimal(str(value)).normalize()
    if isinstance(value, datetime | date):
        return _canonical_text(value.isoformat())
    if value is None:
        return None
    return _canonical_text(str(value))


def _sort_key(row: tuple) -> str:
    return "\x00".join(str(_normalize(cell)) for cell in row)


def _row_contains(expected_row: tuple, actual_row: tuple) -> bool:
    """Every value the key asks for appears somewhere in the returned row."""
    unclaimed = list(actual_row)
    for want in expected_row:
        for index, got in enumerate(unclaimed):
            if _cells_equal(want, got):
                unclaimed.pop(index)
                break
        else:
            return False
    return True


def values_match(
    oracle: dict,
    actual_rows: list[dict] | None,
    *,
    ordered: bool = False,
    spec: CaseScoringSpec | None = None,
    total_row_count: int | None = None,
    truncated: bool | None = None,
    actual_member_schema: tuple[str, ...] | None = None,
) -> bool:
    """True when the module's rows carry the same values as the answer key."""
    if spec is not None:
        if actual_rows is None:
            return False
        if len(actual_rows) != spec.expected_row_count:
            return False
        if oracle.get("row_count") != spec.expected_row_count:
            return False
        if total_row_count != spec.expected_total_row_count:
            return False
        if truncated is not spec.expected_truncated:
            return False

        oracle_columns = oracle.get("columns", [])
        mapping_by_column = {mapping.oracle_column: mapping for mapping in spec.member_mappings}
        if len(mapping_by_column) != len(spec.member_mappings):
            return False
        if set(oracle_columns) != set(mapping_by_column):
            return False
        if spec.expected_row_count > 1 and not spec.row_key:
            return False
        if not set(spec.row_key).issubset(mapping_by_column):
            return False
        if actual_member_schema is None:
            if not actual_rows:
                return False
            schema_members = set(actual_rows[0])
        else:
            schema_members = set(actual_member_schema)
            if len(schema_members) != len(actual_member_schema):
                return False
        # Which declared route each oracle column actually arrived on. A column
        # with no route present is a schema mismatch, not a wrong value.
        member_by_column = {
            column: mapping.resolve_member(schema_members)
            for column, mapping in mapping_by_column.items()
        }
        if any(member is None for member in member_by_column.values()):
            return False
        result_members = {member for member in member_by_column.values() if member is not None}
        if len(result_members) != len(spec.member_mappings):
            return False
        if not (schema_members - result_members).issubset(spec.metadata_allowlist):
            return False
        if any(set(row) != schema_members for row in actual_rows):
            return False

        oracle_rows = oracle.get("rows", [])
        if len(oracle_rows) != spec.expected_row_count:
            return False
        expected_rows: list[dict[str, object]] = []
        for oracle_row in oracle_rows:
            if len(oracle_row) != len(oracle_columns):
                return False
            expected_rows.append(dict(zip(oracle_columns, oracle_row, strict=True)))

        paired_rows: list[tuple[dict[str, object], dict[str, object]]]
        if spec.ordered or not spec.row_key:
            paired_rows = list(zip(expected_rows, actual_rows, strict=True))
        else:
            try:
                expected_by_key = {
                    tuple(
                        _normalize_typed(row[column], mapping_by_column[column].value_kind)
                        for column in spec.row_key
                    ): row
                    for row in expected_rows
                }
                actual_by_key = {
                    tuple(
                        _normalize_typed(
                            row[member_by_column[column]],
                            mapping_by_column[column].value_kind,
                        )
                        for column in spec.row_key
                    ): row
                    for row in actual_rows
                }
            except (KeyError, ValueError, ArithmeticError):
                return False
            if len(expected_by_key) != len(expected_rows) or len(actual_by_key) != len(actual_rows):
                return False
            if expected_by_key.keys() != actual_by_key.keys():
                return False
            paired_rows = [(expected_by_key[key], actual_by_key[key]) for key in expected_by_key]

        for expected_by_column, actual_row in paired_rows:
            for mapping in spec.member_mappings:
                if not _typed_cells_equal(
                    expected_by_column[mapping.oracle_column],
                    actual_row[member_by_column[mapping.oracle_column]],
                    mapping.value_kind,
                ):
                    return False
        return True

    if actual_rows is None:
        return False
    expected = [tuple(row) for row in oracle["rows"]]
    actual = [tuple(row.values()) for row in actual_rows]
    if len(expected) != len(actual):
        return False
    if ordered:
        return all(_row_contains(e, a) for e, a in zip(expected, actual, strict=True))

    # The module returns descriptive columns the key does not ask for (a job id
    # PLUS its department and status), so a key row must be CONTAINED in a
    # returned row rather than equal to it. Greedy matching is safe here: each
    # key row consumes one distinct returned row.
    remaining = list(actual)
    for want in sorted(expected, key=_sort_key):
        for index, candidate in enumerate(remaining):
            if _row_contains(want, candidate):
                remaining.pop(index)
                break
        else:
            return False
    return True


def _total_count_matches(
    oracle: dict, actual_rows: list[dict] | None, total_row_count: int | None
) -> bool:
    """The answer is bigger than any plan may return, so judge the total.

    Showing nothing still fails: "597 exist" is not an answer to "which ones".
    """
    if not actual_rows or total_row_count is None:
        return False
    return _cells_equal(oracle["rows"][0][0], total_row_count)


def spec_schema_mismatch(
    spec: CaseScoringSpec,
    actual_member_schema: tuple[str, ...] | None,
    actual_rows: list[dict] | None,
) -> str | None:
    """Describe how the result's members differ from the ones the spec names.

    `values_match` rejects a schema mismatch and a wrong number with the same
    bare False, so without this both read as "values differ from answer key" --
    which points at the number when the number was never compared.

    Naming the members says what happened and nothing more. Which SIDE is
    wrong is not decidable here: the spec may name a member no correct answer
    can carry, or the module may have taken a route that does not answer the
    question at all. Read the members against the bundle and the case's own
    grounding before touching either.
    """
    # An empty schema carries no member to compare, so every mapping would read
    # as "missing" and the detail would accuse the answer key of naming members
    # a result never had. Fall through to the value wording instead.
    if actual_member_schema:
        schema = set(actual_member_schema)
    elif actual_member_schema is None and actual_rows:
        schema = set(actual_rows[0])
    else:
        return None

    accepted = {name for mapping in spec.member_mappings for name in mapping.accepted_members}
    missing = [
        " or ".join(mapping.accepted_members)
        for mapping in spec.member_mappings
        if mapping.resolve_member(schema) is None
    ]
    # Alternatives say either member MAY carry the column, so both present
    # leaves it undecided which one is the answer. That is its own finding --
    # without it the row falls through to "values differ" and accuses a number
    # the scorer never looked at.
    undecided = [
        f"{mapping.oracle_column} ({', '.join(present)})"
        for mapping in spec.member_mappings
        if len(present := [n for n in mapping.accepted_members if n in schema]) > 1
    ]
    unexpected = sorted(schema - accepted - set(spec.metadata_allowlist))
    if not missing and not unexpected and not undecided:
        return None

    parts = []
    if missing:
        parts.append(f"spec expects {', '.join(missing)}")
    if undecided:
        parts.append(f"result carries more than one declared route for {'; '.join(undecided)}")
    if unexpected:
        parts.append(f"result carries {', '.join(unexpected)}")
    return f"result members do not match the scoring spec: {'; '.join(parts)}"


def score_case(
    case: dict,
    oracle: dict | None,
    outcome_actual: str,
    actual_rows: list[dict] | None,
    *,
    ordered: bool = False,
    total_row_count: int | None = None,
    truncated: bool | None = None,
    actual_member_schema: tuple[str, ...] | None = None,
    scoring_spec: CaseScoringSpec | None = None,
    gate: bool = False,
) -> CaseScore:
    """Judge one case. `oracle` is None for behaviour-only cases."""
    expected = case["draft_expected"]
    outcome_match = expected == outcome_actual

    if oracle is None:
        if expected == "answered":
            return CaseScore(
                case_id=case["id"],
                outcome_expected=expected,
                outcome_actual=outcome_actual,
                outcome_match=outcome_match,
                literal_match=False,
                passed=False,
                detail="answered case lacks an independent oracle",
            )
        detail = "behaviour only" if outcome_match else f"expected {expected}, got {outcome_actual}"
        return CaseScore(
            case_id=case["id"],
            outcome_expected=expected,
            outcome_actual=outcome_actual,
            outcome_match=outcome_match,
            literal_match=None,
            passed=outcome_match,
            detail=detail,
        )

    if gate and scoring_spec is None:
        literal_match = False
        detail = (
            f"expected {expected}, got {outcome_actual}"
            if not outcome_match
            else "missing frozen CaseScoringSpec"
        )
        return CaseScore(
            case_id=case["id"],
            outcome_expected=expected,
            outcome_actual=outcome_actual,
            outcome_match=outcome_match,
            literal_match=literal_match,
            passed=False,
            detail=detail,
        )

    if scoring_spec is None:
        if case.get("scoring") == "total_count":
            literal_match = _total_count_matches(oracle, actual_rows, total_row_count)
        else:
            literal_match = values_match(oracle, actual_rows, ordered=ordered)
    else:
        literal_match = values_match(
            oracle,
            actual_rows,
            ordered=ordered,
            spec=scoring_spec,
            total_row_count=total_row_count,
            truncated=truncated,
            actual_member_schema=actual_member_schema,
        )
    if not outcome_match:
        detail = f"expected {expected}, got {outcome_actual}"
    elif not literal_match and case.get("scoring") == "total_count":
        detail = f"expected {oracle['rows'][0][0]} matching rows, module reported {total_row_count}"
    elif not literal_match:
        mismatch = (
            spec_schema_mismatch(scoring_spec, actual_member_schema, actual_rows)
            if scoring_spec is not None
            else None
        )
        detail = mismatch or (
            f"values differ from answer key ({oracle['row_count']} expected row(s))"
        )
    else:
        detail = "ok"
    return CaseScore(
        case_id=case["id"],
        outcome_expected=expected,
        outcome_actual=outcome_actual,
        outcome_match=outcome_match,
        literal_match=literal_match,
        passed=outcome_match and literal_match,
        detail=detail,
    )


def score_execution(
    case: dict,
    scoring_spec: CaseScoringSpec,
    oracle: dict,
    event: BusinessQueryExecutionEvent,
) -> CaseScore:
    """Score only integrity-checked executor evidence at the public gate seam."""
    return score_case(
        case,
        oracle,
        "answered",
        list(event.result_rows),
        total_row_count=event.total_row_count,
        truncated=event.truncated,
        actual_member_schema=tuple(member.name for member in event.result_members),
        scoring_spec=scoring_spec,
        gate=True,
    )
