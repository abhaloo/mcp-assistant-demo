"""Canonical, bounded structured-record query clauses.

The record executor accepts these clauses rather than an open map of field
names to arbitrary query language objects.  ``normalize_legacy_filters`` is
an intentionally narrow transition adapter for the observed ``$gte``/``$lt``
emission shape; it produces the same canonical clauses or rejects the input.
It is not a general MongoDB or SQL expression parser.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

FilterOperator = Literal["eq", "gt", "gte", "lt", "lte"]
SortDirection = Literal["asc", "desc"]
FilterValue = str | int

_MAX_FILTER_CLAUSES = 8
_LEGACY_OPERATOR_MAP: dict[str, FilterOperator] = {
    "$eq": "eq",
    "$gt": "gt",
    "$gte": "gte",
    "$lt": "lt",
    "$lte": "lte",
}

# Tool feedback must explain the correction without echoing field values,
# authorization state, SQL, or a rejected record identifier.
SANITIZED_QUERY_CORRECTION = (
    "Use the capability card and send each filter as field, operator, and value; "
    "do not use operator dictionaries."
)


class FilterClause(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    field: str = Field(min_length=1, max_length=64)
    operator: FilterOperator = "eq"
    value: FilterValue


class SortClause(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    field: str = Field(min_length=1, max_length=64)
    direction: SortDirection = "asc"


class CanonicalQueryError(ValueError):
    """Raised when a caller bypasses the public schema with an unsafe query.

    The executor deliberately maps this to its generic access error so this
    internal distinction never becomes a probing oracle at the API boundary.
    """


def normalize_business_datetime(value: str, business_timezone: str | None) -> dt.datetime:
    """Interpret a datetime in the manifest's business timezone.

    Naive input is local business time; offset/Z input is converted to that
    same timezone.  The virtual record projections currently store naive
    business-local datetimes, so the bound value intentionally has its tzinfo
    removed.  A paired MySQL journey remains required before claiming a
    storage/session guarantee beyond this compiler contract.
    """
    if business_timezone is None:
        raise CanonicalQueryError("datetime filters require a manifest business timezone")
    try:
        timezone = ZoneInfo(business_timezone)
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise CanonicalQueryError("invalid business datetime") from error
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone).replace(tzinfo=None)
    return parsed.astimezone(timezone).replace(tzinfo=None)


def compile_half_open_business_period(
    field: str, start: str, end: str, business_timezone: str | None
) -> list[FilterClause]:
    """Compile a literal business period to canonical ``[start, end)`` clauses."""
    start_value = normalize_business_datetime(start, business_timezone)
    end_value = normalize_business_datetime(end, business_timezone)
    if start_value >= end_value:
        raise CanonicalQueryError("business period must have start before end")
    return [
        FilterClause(field=field, operator="gte", value=start_value.isoformat()),
        FilterClause(field=field, operator="lt", value=end_value.isoformat()),
    ]


def validate_canonical_filter_clauses(
    clauses: list[FilterClause], business_timezone: str | None, datetime_fields: set[str]
) -> list[FilterClause]:
    """Reject ambiguous/empty filter sets and normalize datetime bounds.

    A canonical executor accepts only explicit typed clauses.  Legacy maps
    are converted at the public Pydantic boundary; direct executor callers
    never receive that compatibility path.
    """
    if len(clauses) > _MAX_FILTER_CLAUSES:
        raise CanonicalQueryError("too many filter clauses")
    seen: set[tuple[str, str]] = set()
    by_field: dict[str, list[FilterClause]] = {}
    normalized: list[FilterClause] = []
    for clause in clauses:
        if not isinstance(clause, FilterClause):
            raise CanonicalQueryError("filters must be explicit clauses")
        key = (clause.field, clause.operator)
        if key in seen:
            raise CanonicalQueryError("duplicate filter clause")
        seen.add(key)
        if clause.field in datetime_fields:
            if not isinstance(clause.value, str):
                raise CanonicalQueryError("datetime filter value must be text")
            value = normalize_business_datetime(clause.value, business_timezone)
            normalized_clause = FilterClause(
                field=clause.field, operator=clause.operator, value=value.isoformat()
            )
        else:
            normalized_clause = clause
        normalized.append(normalized_clause)
        by_field.setdefault(clause.field, []).append(normalized_clause)

    for field, field_clauses in by_field.items():
        if field not in datetime_fields:
            continue
        operators = {clause.operator for clause in field_clauses}
        if "eq" in operators and len(operators) != 1:
            raise CanonicalQueryError("equality cannot be combined with period bounds")
        lower = [clause for clause in field_clauses if clause.operator in {"gt", "gte"}]
        upper = [clause for clause in field_clauses if clause.operator in {"lt", "lte"}]
        if len(lower) > 1 or len(upper) > 1:
            raise CanonicalQueryError("conflicting business period bounds")
        if lower and upper:
            lower_value = dt.datetime.fromisoformat(str(lower[0].value))
            upper_value = dt.datetime.fromisoformat(str(upper[0].value))
            if lower_value >= upper_value:
                raise CanonicalQueryError("business period must have start before end")
    return normalized


def normalize_legacy_filters(value: object) -> list[dict[str, object]] | object:
    """Convert only the legacy filter-map shapes into explicit clauses.

    A normal scalar map value means equality.  A mapping value may contain
    only the five observed dollar-prefixed comparison operators; each becomes
    one clause.  Empty maps, nested expressions, unknown operators, booleans,
    and more than eight clauses are rejected by returning the original value,
    which the strict public Pydantic schema then refuses.
    """
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return value

    clauses: list[dict[str, object]] = []
    for field, raw_value in value.items():
        if not isinstance(field, str) or not field:
            return value
        if isinstance(raw_value, bool):
            return value
        if isinstance(raw_value, (str, int)):
            clauses.append({"field": field, "operator": "eq", "value": raw_value})
            continue
        if not isinstance(raw_value, dict) or not raw_value:
            return value
        for raw_operator, operand in raw_value.items():
            operator = None
            if isinstance(raw_operator, str):
                operator = _LEGACY_OPERATOR_MAP.get(raw_operator)
            if operator is None or isinstance(operand, bool) or not isinstance(operand, (str, int)):
                return value
            clauses.append({"field": field, "operator": operator, "value": operand})
            if len(clauses) > _MAX_FILTER_CLAUSES:
                return value
    return clauses


def legacy_filter_normalization_count(raw_value: object, normalized_value: object) -> int:
    """Return a bounded, value-free transition metric for legacy map input."""
    if isinstance(raw_value, dict) and isinstance(normalized_value, list):
        return len(normalized_value)
    return 0


def normalize_legacy_sort(value: object) -> dict[str, object] | object:
    """Treat the legacy bare sort name as an explicit ascending clause."""
    if isinstance(value, str):
        return {"field": value, "direction": "asc"}
    return value
