"""Typed BusinessQueryPlan algebra (Cube-shaped, closed relative periods)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection
from datetime import date, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.business_query.plan.derived_sets import (
    _SET_ID_PATTERN,
    MAX_DERIVED_SETS,
    ORDERED_MODES,
    SetMode,
    format_violations,
    validate_rank_limit,
)
from app.business_query.plan.filter_tree import (
    AttributePredicate,
    FilterGroup,
    _collect_filter_members,
    _is_neutral_group_node,
    iter_filter_leaves,
)
from app.business_query.plan.time_groups import TimeGroupKey, time_group_violations

RelativeRange = Literal[
    "today",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "this_year",
    "last_year",
    "all_time",
]
# A breakdown by time over all the data: no date filter, never an invented range.
ALL_TIME: RelativeRange = "all_time"
DetailRevisionMode = Literal["recorded", "current", "as_of", "exact"]


class DetailSelection(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    family: str
    revision_mode: DetailRevisionMode = "recorded"
    revision_hash: str | None = None
    as_of: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_family(cls, data: object) -> object:
        if isinstance(data, str):
            return {"family": data, "revision_mode": "recorded"}
        if isinstance(data, dict):
            data = {**data}
            if "family" not in data:
                for alias in ("family_id", "family_key", "name", "key"):
                    if alias in data:
                        data["family"] = data.pop(alias)
                        break
        return data


class BusinessPeriod(BaseModel):
    """Half-open [start, end) resolved against the bundle business timezone."""

    model_config = ConfigDict(strict=True, extra="forbid")
    time_dimension: str
    relative: RelativeRange | None = None
    on: date | None = None
    since: date | None = None
    between: tuple[date, date] | None = None
    granularity: Literal["day", "week", "month", "quarter", "year"] | None = None

    @field_validator("on", "since", mode="before")
    @classmethod
    def _parse_iso_date(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        return value

    @field_validator("between", mode="before")
    @classmethod
    def _accept_start_end_object(cls, value: object) -> object:
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
            value = value[0]
        if isinstance(value, dict) and set(value) == {"start", "end"}:
            value = (value["start"], value["end"])
        if isinstance(value, list | tuple) and len(value) == 2:
            try:
                return tuple(
                    date.fromisoformat(bound) if isinstance(bound, str) else bound
                    for bound in value
                )
            except ValueError:
                return value
        return value

    @model_validator(mode="after")
    def _exactly_one_range(self) -> BusinessPeriod:
        set_count = sum(x is not None for x in (self.relative, self.on, self.since, self.between))
        if set_count != 1:
            raise ValueError(
                "exactly one of relative/on/since/between is required. When the question "
                "names no date range and the period carries a granularity, set relative "
                "to all_time"
            )
        if self.between is not None and self.between[1] < self.between[0]:
            raise ValueError("between end must not precede start")
        return self


CompareShift = Literal["previous_period", "same_period_last_year"]


class OrderClause(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    member: str
    direction: Literal["asc", "desc"]

    @model_validator(mode="before")
    @classmethod
    def _alias_member(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = {**data}
        if "member" not in data:
            for alias in ("measure", "field"):
                if alias in data:
                    data["member"] = data.pop(alias)
                    break
        return data


_MIN_PLAN_LIMIT = 1
_MAX_PLAN_LIMIT = 50


class DerivedSet(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    mode: SetMode
    key: str
    plan: BusinessQueryPlan

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_payload(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        mode = data.get("mode")
        plan_data = data.get("plan")
        if mode in ORDERED_MODES:
            limit = (
                plan_data.get("limit")
                if isinstance(plan_data, dict)
                else getattr(plan_data, "limit", None)
            )
            if type(limit) is not int or not _MIN_PLAN_LIMIT <= limit <= _MAX_PLAN_LIMIT:
                raise ValueError(
                    f"set mode {mode} requires an explicit limit "
                    f"from {_MIN_PLAN_LIMIT} to {_MAX_PLAN_LIMIT}"
                )
        return data

    @field_validator("id")
    @classmethod
    def _validate_id_syntax(cls, value: str) -> str:
        if not _SET_ID_PATTERN.match(value):
            raise ValueError(f"derived set id '{value}' must match ^[A-Za-z][A-Za-z0-9_]{{0,31}}$")
        return value


class BusinessQueryPlan(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    bucket_set: str | None = None
    anchor: str | None = None
    filters: FilterGroup | None = None
    having: FilterGroup | None = None
    period: BusinessPeriod | None = None
    compare_to: BusinessPeriod | CompareShift | None = None
    grain: Literal["scalar", "grouped", "entity_rows"]
    order: list[OrderClause] = Field(default_factory=list)
    attribute_predicates: list[AttributePredicate] = Field(default_factory=list)
    detail_selections: list[DetailSelection] = Field(default_factory=list)
    limit: int = Field(default=20, ge=_MIN_PLAN_LIMIT, le=_MAX_PLAN_LIMIT)
    derived_sets: tuple[DerivedSet, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _normalize_period_and_date_filters(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = {**data}
        data = cls._normalize_filter_operators(data)
        limit = data.get("limit")
        if isinstance(limit, int) and not isinstance(limit, bool):
            data["limit"] = min(_MAX_PLAN_LIMIT, max(_MIN_PLAN_LIMIT, limit))
        for key in ("filters", "having"):
            group = data.get(key)
            if isinstance(group, list):
                data[key] = {"all": group}
        for key in ("filters", "having"):
            if _is_neutral_group_node(data.get(key)):
                data[key] = None
        lifted = cls._take_date_filters(data)
        if lifted is not None and _is_neutral_group_node(data.get("filters")):
            data["filters"] = None
        period = data.get("period")
        if isinstance(period, dict):
            data["period"] = cls._collapse_period(period)
        elif period is None and lifted is not None:
            data["period"] = lifted
        if isinstance(data.get("compare_to"), dict):
            data["compare_to"] = cls._collapse_period(data["compare_to"])
        return data

    @staticmethod
    def _normalize_filter_operators(data: dict) -> dict:
        for key in ("filters", "having"):
            group = data.get(key)
            if group is not None:
                data[key] = BusinessQueryPlan._normalize_filter_group(group)
        return data

    @staticmethod
    def _normalize_filter_group(group: object) -> object:
        if isinstance(group, list):
            return {"all": [BusinessQueryPlan._normalize_filter_node(node) for node in group]}
        if not isinstance(group, dict):
            return group
        normalized = {**group}
        for key in ("all", "any"):
            members = normalized.get(key)
            if isinstance(members, list):
                normalized[key] = [
                    BusinessQueryPlan._normalize_filter_node(node) for node in members
                ]
        return normalized

    @staticmethod
    def _normalize_filter_node(node: object) -> object:
        if not isinstance(node, dict):
            return node
        if "all" in node or "any" in node:
            return BusinessQueryPlan._normalize_filter_group(node)
        data = {**node}
        if "member" not in data:
            for alias in ("dimension", "field", "name"):
                if alias in data:
                    data["member"] = data.pop(alias)
                    break
        operator = data.get("operator")
        if operator == ">":
            data["operator"] = "gt"
        elif operator in {"equals", "="}:
            data["operator"] = "eq"
        return data

    @staticmethod
    def _collapse_period(period: dict) -> dict:
        if period.get("between") is None:
            return period
        return {
            key: value
            for key, value in period.items()
            if key not in {"since", "on", "relative"} or value is None
        }

    @staticmethod
    def _take_date_filters(data: dict) -> dict | None:
        group = data.get("filters")
        if not isinstance(group, dict):
            return None
        first: dict | None = None
        cleaned = {**group}
        for key in ("all", "any"):
            members = group.get(key)
            if not isinstance(members, list):
                continue
            kept = []
            for entry in members:
                candidate = BusinessQueryPlan._as_period(entry) if isinstance(entry, dict) else None
                if candidate is None:
                    kept.append(entry)
                    continue
                first = first or candidate
            cleaned[key] = kept
        if first is not None:
            data["filters"] = cleaned
        return first

    @staticmethod
    def _as_period(entry: dict) -> dict | None:
        operator, values = entry.get("operator"), entry.get("values")
        if not isinstance(values, list) or not values:
            return None
        if operator == "between" and len(values) == 2:
            range_field: dict[str, object] = {"between": (values[0], values[1])}
        elif operator in {"since", "on"}:
            range_field = {operator: values[0]}
        else:
            return None
        return {**range_field, "time_dimension": entry.get("member")}

    @field_validator("measures", "dimensions", "order", "detail_selections", mode="before")
    @classmethod
    def _null_list_means_empty(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("derived_sets", mode="before")
    @classmethod
    def _null_tuple_means_empty(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("limit", mode="before")
    @classmethod
    def _null_limit_means_default(cls, value: object) -> object:
        return 20 if value is None else value

    @model_validator(mode="after")
    def _grain_shape(self) -> BusinessQueryPlan:
        scalar_has_grouping = self.dimensions or self.bucket_set
        if self.grain == "scalar" and (len(self.measures) != 1 or scalar_has_grouping):
            raise ValueError("scalar grain requires exactly one measure and no grouping")
        if self.grain == "grouped" and not self.measures:
            raise ValueError("grouped grain requires at least one measure")
        if self.grain == "grouped" and not (self.dimensions or self.bucket_set):
            raise ValueError("grouped grain requires a dimension or bucket_set")
        if self.grain == "entity_rows" and self.measures:
            raise ValueError("entity_rows returns rows, not aggregates")
        if self.compare_to is not None:
            if self.period is None:
                raise ValueError("compare_to requires a period")
            if isinstance(self.compare_to, BusinessPeriod):
                if self.compare_to.time_dimension != self.period.time_dimension:
                    raise ValueError("compare_to time_dimension must match period time_dimension")
                if self.compare_to.granularity is not None:
                    raise ValueError("compare_to cannot carry a granularity")
            if self.period.granularity is not None:
                raise ValueError("comparison plan cannot carry a period granularity")
            if self.grain == "entity_rows":
                raise ValueError("comparison plan cannot use entity_rows grain")
            if len(self.measures) != 1:
                raise ValueError("comparison plan requires exactly one measure")
        _validate_derived_sets(self)
        return self

    @model_validator(mode="after")
    def _time_grouping_names_a_granularity(self, info: ValidationInfo) -> BusinessQueryPlan:
        """A grouped plan groups a time member by a calendar bucket, never by each
        raw timestamp.

        The period's time dimension is always a time member. The validation context
        can name more (``TIME_DIMENSIONS_CONTEXT``); the planner reads them from the
        card. The planner's repair round receives this text verbatim, so each branch
        it names is a valid plan when followed word for word."""
        context = info.context if isinstance(info.context, dict) else {}
        member = raw_time_dimension(self, context.get(TIME_DIMENSIONS_CONTEXT, ()))
        if member is None:
            return self
        if self.period is not None and self.period.time_dimension == member:
            raise ValueError(
                "a grouped plan by its period's time dimension names a granularity. "
                "For one total over the period, remove the time dimension from "
                "dimensions, and when no dimension is left, set grain to scalar. "
                "For a breakdown by time, set the period's granularity "
                "(day, week, month, quarter or year)"
            )
        raise ValueError(
            f"a grouped plan by the time dimension {member} names a granularity. "
            f"For one total, remove {member} from dimensions, and when no dimension "
            "is left, set grain to scalar. For a breakdown by time, "
            f"set period with time_dimension {member}, a granularity "
            "(day, week, month, quarter or year), and exactly one range: "
            "relative, on, since or between; use relative all_time when the question "
            "names no date range"
        )

    @model_validator(mode="after")
    def _all_time_is_a_breakdown_by_time(self) -> BusinessQueryPlan:
        """relative all_time sets no date filter, so it is valid only as a grouped
        breakdown by the period's own time dimension. One total over all the data is
        period null. The repair round receives this text verbatim."""
        if isinstance(self.compare_to, BusinessPeriod) and self.compare_to.relative == ALL_TIME:
            raise ValueError(
                "compare_to cannot be all_time: a comparison needs two bounded ranges. "
                "Set compare_to to previous_period, same_period_last_year, or a period "
                "with one bounded range"
            )
        period = self.period
        if period is None or period.relative != ALL_TIME:
            return self
        if (
            self.grain == "grouped"
            and period.granularity is not None
            and period.time_dimension in self.dimensions
        ):
            return self
        raise ValueError(
            "relative all_time is a breakdown by time over all the data. For that "
            "breakdown, set grain to grouped, list the period's time_dimension in "
            "dimensions, and set its granularity (day, week, month, quarter or year). "
            "For no date limit without a breakdown by time, set period to null"
        )

    @model_validator(mode="after")
    def _anchor_is_projected(self) -> BusinessQueryPlan:
        if self.anchor is None:
            return self
        owners = {m.split(".", 1)[0] for m in (*self.dimensions, *self.measures)}
        if self.anchor not in owners:
            raise ValueError("anchor must be a projected resource")
        return self


TIME_DIMENSIONS_CONTEXT = "time_dimensions"


def raw_time_dimension(plan: BusinessQueryPlan, time_dimensions: Collection[str]) -> str | None:
    """The first time member a grouped plan groups by each raw timestamp, if any.

    Only the period's own time dimension can carry a granularity, so every other
    time member in a grouped plan is grouped raw. The period's time dimension is a
    time member even when ``time_dimensions`` does not name it."""
    if plan.grain != "grouped":
        return None
    period = plan.period
    known = set(time_dimensions)
    bucketed = None
    if period is not None:
        known.add(period.time_dimension)
        if period.granularity is not None:
            bucketed = period.time_dimension
    return next((m for m in plan.dimensions if m in known and m != bucketed), None)


def _validate_derived_sets(plan: BusinessQueryPlan) -> None:
    """Check set declarations, references, chains, and inner shapes, and report every
    broken rule in one message.

    A plan checks references only against the sets it declares itself: a chained
    inner plan carries a reference its owner resolves. The root plan checks its own
    filters and every inner plan's filters against its declaration list.

    Lives here (not in plan/derived_sets.py) because it inspects
    ``BusinessQueryPlan``/``DerivedSet`` shape directly and is called only from
    ``_grain_shape`` above; see docs/superpowers/import-cycles-baseline.json."""
    sets = plan.derived_sets
    violations: list[str] = []
    if len(sets) > MAX_DERIVED_SETS:
        violations.append(
            f"at most {MAX_DERIVED_SETS} derived sets allowed per plan, got {len(sets)}"
        )
    declared = [item.id for item in sets]
    if len(set(declared)) != len(declared):
        violations.append("derived set ids must be unique")
    if set_reference_ids(plan.having):
        violations.append("set operators are not permitted in having clause")

    position = {set_id: index for index, set_id in enumerate(declared)}
    referenced: set[str] = set()
    if sets:
        for ref in set_reference_ids(plan.filters):
            referenced.add(ref)
            if ref not in position:
                violations.append(f"unknown derived set id: '{ref}'")
    for index, item in enumerate(sets):
        for ref in set_reference_ids(item.plan.filters):
            referenced.add(ref)
            if ref not in position:
                violations.append(f"set '{item.id}' references unknown derived set id: '{ref}'")
            elif position[ref] >= index:
                violations.append(
                    f"set '{item.id}' references '{ref}', which is not declared before it"
                )
            elif time_group_for(sets[position[ref]].key, sets[position[ref]].plan) is not None:
                violations.append(f"set '{item.id}' cannot filter by the time group '{ref}'")
        if set_reference_ids(item.plan.having):
            violations.append(f"set '{item.id}': set operators are not permitted in having clause")
        if set_reference_ids(item.plan.filters) and time_group_for(item.key, item.plan) is not None:
            violations.append(f"time-group set '{item.id}' cannot filter by another set")
    violations.extend(
        f"unreferenced derived set id: '{set_id}'"
        for set_id in declared
        if set_id not in referenced
    )
    for item in sets:
        violations.extend(_inner_shape_violations(item))
    if violations:
        raise ValueError(format_violations(violations))


def set_reference_ids(group: FilterGroup | None) -> list[str]:
    """The set ids named by in_set / not_in_set leaves, in filter order."""
    return [
        str(leaf.values[0])
        for leaf in iter_filter_leaves(group)
        if leaf.operator in {"in_set", "not_in_set"}
    ]


def time_group_for(key: str, plan: BusinessQueryPlan) -> TimeGroupKey | None:
    """The calendar bucket a set selects when its key is its own period's time member."""
    period = plan.period
    if period is None or period.granularity is None or period.time_dimension != key:
        return None
    return TimeGroupKey(time_dimension=key, granularity=period.granularity)


def _inner_shape_violations(item: DerivedSet) -> list[str]:
    inner = item.plan
    group = time_group_for(item.key, inner)
    prefix = f"set '{item.id}': "
    out: list[str] = []
    if inner.derived_sets:
        out.append(
            prefix + "derived sets cannot be nested; declare the set earlier in the same "
            "list and reference it by id"
        )
    if inner.grain == "scalar":
        out.append(prefix + "derived set inner plan grain cannot be scalar")
    if inner.dimensions != [item.key]:
        out.append(
            prefix + f"derived set inner plan must project exactly [key], got {inner.dimensions}"
        )
    out.extend(
        prefix + text for text in _disallowed_inner_clauses(inner, time_group=group is not None)
    )
    if group is not None:
        out.extend(
            prefix + text
            for text in time_group_violations(
                mode=item.mode, grain=inner.grain, measure_count=len(inner.measures)
            )
        )
    if item.mode == "complete":
        out.extend(prefix + text for text in _complete_violations(inner))
    elif item.mode == "ranked":
        out.extend(prefix + text for text in _ranked_violations(item))
    elif item.mode == "pick":
        out.extend(prefix + text for text in _pick_violations(item))
    return out


def _disallowed_inner_clauses(inner: BusinessQueryPlan, *, time_group: bool = False) -> list[str]:
    out: list[str] = []
    if inner.bucket_set is not None:
        out.append("derived set inner plan cannot have a bucket_set")
    if inner.detail_selections:
        out.append("derived set inner plan cannot have detail_selections")
    if inner.period is not None and inner.period.granularity is not None and not time_group:
        out.append("derived set inner plan cannot have period granularity")
    if inner.compare_to is not None:
        out.append("derived set inner plan cannot have compare_to")
    return out


def _complete_violations(inner: BusinessQueryPlan) -> list[str]:
    out: list[str] = []
    if inner.order:
        out.append("complete derived set cannot carry an order")
    if inner.grain == "entity_rows" and inner.measures:
        out.append("entity_rows complete derived set cannot have measures")
    return out


_ORDERED_SET_TIEBREAK_CLAUSES = 2


def _ranked_violations(item: DerivedSet) -> list[str]:
    inner = item.plan
    out: list[str] = []
    if inner.grain != "grouped":
        out.append("ranked derived set requires grouped grain")
    if len(inner.measures) != 1:
        out.append(f"ranked derived set requires exactly one measure, got {len(inner.measures)}")
    try:
        validate_rank_limit(inner.limit)
    except ValueError as exc:
        out.append(str(exc))
    if len(inner.order) != _ORDERED_SET_TIEBREAK_CLAUSES:
        out.append("ranked derived set requires exactly two order clauses (measure desc, key asc)")
        return out
    measure = inner.measures[0] if inner.measures else ""
    if inner.order[0].member != measure or inner.order[0].direction != "desc":
        out.append(f"ranked set first order clause must be measure '{measure}' descending")
    if inner.order[1].member != item.key or inner.order[1].direction != "asc":
        out.append(f"ranked set second order clause must be key '{item.key}' ascending")
    return out


def _pick_violations(item: DerivedSet) -> list[str]:
    inner = item.plan
    out: list[str] = []
    # The limit was already checked on the raw payload; a constructed plan always
    # carries one in range.
    clauses = list(inner.order)
    # The key ascending is the tiebreak the compiler appends; written out, it is harmless.
    if (
        len(clauses) == _ORDERED_SET_TIEBREAK_CLAUSES
        and clauses[1].member == item.key
        and clauses[1].direction == "asc"
    ):
        clauses = clauses[:1]
    if len(clauses) != 1:
        out.append(
            "pick orders by exactly one clause [{member, direction}]; "
            "the key ascending is appended for you"
        )
        return out
    basis = clauses[0].member
    if inner.grain == "entity_rows":
        if inner.measures:
            out.append("entity_rows pick cannot have measures")
        if basis in inner.measures:
            out.append(f"entity_rows pick orders by a dimension, got measure '{basis}'")
    elif len(inner.measures) != 1:
        out.append(f"grouped pick requires exactly one measure, got {len(inner.measures)}")
    elif basis != inner.measures[0]:
        out.append(f"grouped pick orders by its measure '{inner.measures[0]}', got '{basis}'")
    return out


def local_plan_member_names(plan: BusinessQueryPlan) -> set[str]:
    """Every member name a single plan node touches, without nested sets."""
    names: set[str] = set(plan.measures) | set(plan.dimensions)
    if plan.bucket_set is not None:
        names.add(plan.bucket_set)
    names |= _collect_filter_members(plan.filters)
    names |= _collect_filter_members(plan.having)
    for pred in plan.attribute_predicates:
        names.add(pred.family_key)
    names.update(selection.family for selection in plan.detail_selections)
    if plan.period is not None:
        names.add(plan.period.time_dimension)
    for clause in plan.order:
        names.add(clause.member)
    return names


def plan_member_names(plan: BusinessQueryPlan) -> set[str]:
    """Every member name a plan touches, recursively across derived sets."""
    names = local_plan_member_names(plan)
    for derived_set in plan.derived_sets:
        names |= plan_member_names(derived_set.plan)
    return names


def canonical_plan_payload(plan: BusinessQueryPlan) -> dict[str, Any]:
    """Return canonical plan dict with empty derived_sets omitted recursively."""
    payload = plan.model_dump(mode="json")
    if plan.compare_to is None:
        payload.pop("compare_to", None)
    if not plan.derived_sets:
        payload.pop("derived_sets", None)
    else:
        for encoded, item in zip(payload["derived_sets"], plan.derived_sets, strict=True):
            encoded["plan"] = canonical_plan_payload(item.plan)
    return payload


def plan_fingerprint(plan: BusinessQueryPlan) -> str:
    """Stable sha256 over canonical plan JSON (sorted keys)."""
    payload = canonical_plan_payload(plan)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


DerivedSet.model_rebuild()
BusinessQueryPlan.model_rebuild()
