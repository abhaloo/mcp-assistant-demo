"""Filter tree AST nodes, operator definitions, and canonical tree traversals."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

FilterOperator = Literal[
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "is_null",
    "not_null",
    "contains",
]

SetOperator = Literal["in_set", "not_in_set"]


def _is_neutral_group_node(node: object) -> bool:
    """A group dict whose branches are absent, empty, or themselves neutral."""
    if not isinstance(node, dict) or ("all" not in node and "any" not in node):
        return False
    if any(key in node for key in ("member", "operator", "values", "value")):
        return False
    for key in ("all", "any"):
        members = node.get(key)
        if members is None:
            continue
        if not isinstance(members, list):
            return False
        if any(not _is_neutral_group_node(child) for child in members):
            return False
    return True


class PlanFilter(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    member: str
    operator: FilterOperator | SetOperator
    values: list[str | int | float | bool] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _singular_value_means_values(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = {**data}
        if data.get("operator") == ">":
            data["operator"] = "gt"
        if "values" not in data and "value" in data:
            data["values"] = [data.pop("value")]
        else:
            data.pop("value", None)
        return data

    @model_validator(mode="after")
    def _operator_value_arity(self) -> PlanFilter:
        if self.operator in {"is_null", "not_null"}:
            if self.values:
                raise ValueError(f"{self.operator} does not accept values")
        elif self.operator in {"in_set", "not_in_set"}:
            if len(self.values) != 1 or not isinstance(self.values[0], str):
                raise ValueError(f"{self.operator} requires exactly one string value (set id)")
        elif not self.values:
            raise ValueError(f"{self.operator} requires at least one value")
        return self


class AttributePredicate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    family_key: str
    attribute_key: str | None = None
    revision_hash: str | None = None
    definition_revision: str | None = None
    value_kind: Literal[
        "integer", "numeric", "decimal", "currency", "string", "date", "datetime", "boolean", "json"
    ] = "integer"
    operator: FilterOperator
    values: list[str | int | float | bool] = Field(default_factory=list)
    quantifier: Literal["any", "all", "exact"] = "any"

    @model_validator(mode="before")
    @classmethod
    def _singular_value_means_values(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = {**data}
        if "family_key" not in data and "attribute_key" in data:
            data["family_key"] = data["attribute_key"]
        elif "attribute_key" not in data and "family_key" in data:
            data["attribute_key"] = data["family_key"]
        if "revision_hash" not in data and "definition_revision" in data:
            data["revision_hash"] = data["definition_revision"]
        elif "definition_revision" not in data and "revision_hash" in data:
            data["definition_revision"] = data["revision_hash"]
        op_map = {
            ">": "gt",
            ">=": "gte",
            "<": "lt",
            "<=": "lte",
            "=": "eq",
            "==": "eq",
            "!=": "neq",
            "<>": "neq",
        }
        if data.get("operator") in op_map:
            data["operator"] = op_map[data["operator"]]
        if "values" not in data and "value" in data:
            data["values"] = [data.pop("value")]
        else:
            data.pop("value", None)
        return data

    @model_validator(mode="after")
    def _operator_value_arity(self) -> AttributePredicate:
        if self.operator in {"is_null", "not_null"}:
            if self.values:
                raise ValueError(f"{self.operator} does not accept values")
        elif not self.values:
            raise ValueError(f"{self.operator} requires at least one value")
        return self


class FilterGroup(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    all: list[PlanFilter | AttributePredicate | FilterGroup] = Field(default_factory=list)
    any: list[PlanFilter | AttributePredicate | FilterGroup] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_neutral_branches_and_nodes(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "all" not in data and "any" not in data:
            raise ValueError("filter group requires all or any")
        cleaned = dict(data)
        for key in ("all", "any"):
            members = cleaned.get(key)
            if not isinstance(members, list):
                continue
            kept = [node for node in members if not _is_neutral_group_node(node)]
            if kept:
                cleaned[key] = kept
            else:
                del cleaned[key]
        return cleaned

    @model_serializer
    def _serialize_non_empty_branches(self) -> dict[str, list[PlanFilter | FilterGroup]]:
        serialized: dict[str, list[PlanFilter | FilterGroup]] = {}
        if self.all:
            serialized["all"] = self.all
        if self.any:
            serialized["any"] = self.any
        return serialized

    def walk_leaves(self) -> Iterator[PlanFilter | AttributePredicate]:
        """Yield all leaf predicate nodes in depth-first order."""
        yield from iter_filter_leaves(self)


def iter_filter_leaves(
    node: PlanFilter | AttributePredicate | FilterGroup | None,
) -> Iterator[PlanFilter | AttributePredicate]:
    """Yield all leaf predicate nodes (PlanFilter or AttributePredicate) in depth-first order."""
    if node is None:
        return
    if isinstance(node, (PlanFilter, AttributePredicate)):
        yield node
    elif isinstance(node, FilterGroup):
        for child in node.all or []:
            yield from iter_filter_leaves(child)
        for child in node.any or []:
            yield from iter_filter_leaves(child)


def map_filter_tree(
    node: FilterGroup | PlanFilter | AttributePredicate | None,
    transform: Callable[[PlanFilter | AttributePredicate], PlanFilter | AttributePredicate | None],
) -> FilterGroup | PlanFilter | AttributePredicate | None:
    """Recursively transform leaves of a filter tree."""
    if node is None:
        return None
    if isinstance(node, (PlanFilter, AttributePredicate)):
        return transform(node)
    if isinstance(node, FilterGroup):
        new_all = [
            mapped
            for child in (node.all or [])
            if (mapped := map_filter_tree(child, transform)) is not None
        ]
        new_any = [
            mapped
            for child in (node.any or [])
            if (mapped := map_filter_tree(child, transform)) is not None
        ]
        if not new_all and not new_any:
            return None
        payload: dict[str, list[PlanFilter | AttributePredicate | FilterGroup]] = {}
        if new_all:
            payload["all"] = new_all
        if new_any:
            payload["any"] = new_any
        return FilterGroup(**payload)
    return None


def _collect_filter_members(group: FilterGroup | None) -> set[str]:
    if group is None:
        return set()
    names: set[str] = set()

    def walk(node: PlanFilter | AttributePredicate | FilterGroup) -> None:
        if isinstance(node, PlanFilter):
            names.add(node.member)
            return
        if isinstance(node, AttributePredicate):
            names.add(node.family_key)
            return
        for child in node.all:
            walk(child)
        for child in node.any:
            walk(child)

    walk(group)
    return names


def guaranteed_filter_members(group: FilterGroup | None) -> set[str]:
    """Members constrained on every truth path through a filter tree."""
    if group is None:
        return set()

    required: set[str] = set()
    for node in group.all:
        if isinstance(node, PlanFilter):
            required.add(node.member)
        elif isinstance(node, AttributePredicate):
            required.add(node.family_key)
        else:
            required.update(guaranteed_filter_members(node))

    if group.any:
        branches = []
        for node in group.any:
            if isinstance(node, PlanFilter):
                branches.append({node.member})
            elif isinstance(node, AttributePredicate):
                branches.append({node.family_key})
            else:
                branches.append(guaranteed_filter_members(node))
        required.update(set.intersection(*branches))
    return required


def guaranteed_single_equality(group: FilterGroup | None, member: str) -> str | None:
    """Return one equality value only when every truth path fixes it."""
    if group is None:
        return None

    all_values: set[str] = set()
    for node in group.all:
        if isinstance(node, PlanFilter):
            value = (
                str(node.values[0])
                if node.member == member and node.operator == "eq" and len(node.values) == 1
                else None
            )
        elif isinstance(node, AttributePredicate):
            value = (
                str(node.values[0])
                if node.family_key == member and node.operator == "eq" and len(node.values) == 1
                else None
            )
        else:
            value = guaranteed_single_equality(node, member)
        if value is not None:
            all_values.add(value)
    if len(all_values) > 1:
        return None
    if all_values:
        return next(iter(all_values))

    if not group.any:
        return None
    branch_values: list[str] = []
    for node in group.any:
        if isinstance(node, PlanFilter):
            value = (
                str(node.values[0])
                if node.member == member and node.operator == "eq" and len(node.values) == 1
                else None
            )
        elif isinstance(node, AttributePredicate):
            value = (
                str(node.values[0])
                if node.family_key == member and node.operator == "eq" and len(node.values) == 1
                else None
            )
        else:
            value = guaranteed_single_equality(node, member)
        if value is None:
            return None
        branch_values.append(value)
    return branch_values[0] if len(set(branch_values)) == 1 else None
