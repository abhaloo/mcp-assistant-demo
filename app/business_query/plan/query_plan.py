"""Typed BusinessQueryPlan algebra (Cube-shaped, closed relative periods)."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.business_query.plan.derived_sets import _SET_ID_PATTERN, validate_rank_limit
from app.business_query.plan.filter_tree import (
    AttributePredicate,
    FilterGroup,
    _collect_filter_members,
    _is_neutral_group_node,
    iter_filter_leaves,
)

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
]
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
            raise ValueError("exactly one of relative/on/since/between is required")
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


class DerivedSet(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    mode: Literal["complete", "ranked"]
    key: str
    plan: BusinessQueryPlan

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_payload(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        mode = data.get("mode")
        plan_data = data.get("plan")
        if mode == "ranked":
            if isinstance(plan_data, dict):
                validate_rank_limit(plan_data.get("limit"))
            elif isinstance(plan_data, BusinessQueryPlan):
                validate_rank_limit(plan_data.limit)
        return data

    @field_validator("id")
    @classmethod
    def _validate_id_syntax(cls, value: str) -> str:
        if not _SET_ID_PATTERN.match(value):
            raise ValueError(f"derived set id '{value}' must match ^[A-Za-z][A-Za-z0-9_]{{0,31}}$")
        return value


_MIN_PLAN_LIMIT = 1
_MAX_PLAN_LIMIT = 50


class BusinessQueryPlan(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    measures: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    bucket_set: str | None = None
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


def _validate_derived_sets(plan: BusinessQueryPlan) -> None:
    """Validate derived set declarations, filter references, and inner plan shapes.

    Lives here (not in plan/derived_sets.py) because it inspects
    ``BusinessQueryPlan``/``DerivedSet`` shape directly and is called only
    from ``_grain_shape`` above -- moving it out would force
    plan/derived_sets.py to import this module back for the type it already
    defines. See docs/superpowers/import-cycles-baseline.json."""
    if len(plan.derived_sets) > 2:
        raise ValueError(f"at most 2 derived sets allowed per plan, got {len(plan.derived_sets)}")

    declared_ids: list[str] = [s.id for s in plan.derived_sets]
    if len(set(declared_ids)) != len(declared_ids):
        raise ValueError("derived set ids must be unique")

    having_leaves = list(iter_filter_leaves(plan.having))
    for leaf in having_leaves:
        if leaf.operator in {"in_set", "not_in_set"}:
            raise ValueError("set operators are not permitted in having clause")

    filter_leaves = list(iter_filter_leaves(plan.filters))
    referenced_ids: set[str] = set()
    for leaf in filter_leaves:
        if leaf.operator in {"in_set", "not_in_set"}:
            set_id = str(leaf.values[0])
            if set_id not in set(declared_ids):
                raise ValueError(f"unknown derived set id: '{set_id}'")
            referenced_ids.add(set_id)

    for declared_id in declared_ids:
        if declared_id not in referenced_ids:
            raise ValueError(f"unreferenced derived set id: '{declared_id}'")

    for item in plan.derived_sets:
        inner = item.plan
        if inner.derived_sets:
            raise ValueError("derived sets cannot be nested")
        if inner.grain == "scalar":
            raise ValueError("derived set inner plan grain cannot be scalar")
        if inner.grain not in {"grouped", "entity_rows"}:
            raise ValueError(
                f"derived set inner plan grain must be grouped or entity_rows, got '{inner.grain}'"
            )
        if item.key not in inner.dimensions:
            raise ValueError(
                f"derived set key '{item.key}' must be projected in inner plan dimensions"
            )
        if inner.dimensions != [item.key]:
            raise ValueError(
                f"derived set inner plan must project exactly [key], got {inner.dimensions}"
            )
        if inner.bucket_set is not None:
            raise ValueError("derived set inner plan cannot have a bucket_set")
        if inner.detail_selections:
            raise ValueError("derived set inner plan cannot have detail_selections")
        if inner.period is not None and inner.period.granularity is not None:
            raise ValueError("derived set inner plan cannot have period granularity")
        if inner.compare_to is not None:
            raise ValueError("derived set inner plan cannot have compare_to")

        if item.mode == "complete":
            if inner.order:
                raise ValueError("complete derived set cannot carry an order")
            if inner.grain == "entity_rows" and inner.measures:
                raise ValueError("entity_rows complete derived set cannot have measures")
        elif item.mode == "ranked":
            if inner.grain != "grouped":
                raise ValueError("ranked derived set requires grouped grain")
            if len(inner.measures) != 1:
                raise ValueError(
                    f"ranked derived set requires exactly one measure, got {len(inner.measures)}"
                )
            validate_rank_limit(inner.limit)
            if len(inner.order) != 2:
                raise ValueError(
                    "ranked derived set requires exactly two order clauses (measure desc, key asc)"
                )
            if inner.order[0].member != inner.measures[0] or inner.order[0].direction != "desc":
                raise ValueError(
                    f"ranked set first order clause must be measure "
                    f"'{inner.measures[0]}' descending"
                )
            if inner.order[1].member != item.key or inner.order[1].direction != "asc":
                raise ValueError(
                    f"ranked set second order clause must be key '{item.key}' ascending"
                )


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
