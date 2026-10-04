"""JSON-Patch algebra and application for BusinessQueryPlan."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.business_query.plan.query_plan import BusinessQueryPlan

PatchOpType = Literal["add", "remove", "replace"]
RefusalReason = Literal["unknown_path", "index_out_of_range", "invalid_plan"]


class AddPatchOp(BaseModel):
    """Add a value to an array or object property."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    op: Literal["add"] = "add"
    path: str
    value: Any


class RemovePatchOp(BaseModel):
    """Remove an item from an array or an object property."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    op: Literal["remove"] = "remove"
    path: str


class ReplacePatchOp(BaseModel):
    """Replace a value in an array or an object property."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    op: Literal["replace"] = "replace"
    path: str
    value: Any


PatchOp = Annotated[
    AddPatchOp | RemovePatchOp | ReplacePatchOp,
    Field(discriminator="op"),
]


class PlanPatch(BaseModel):
    """Ordered sequence of patch operations targeting a stored plan."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    subject: str
    ops: tuple[PatchOp, ...] = Field(min_length=1, max_length=8)
    mentions: tuple[str, ...] = ()

    @field_validator("ops", "mentions", mode="before")
    @classmethod
    def _coerce_tuples(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value


class PlanPatchRefused(Exception):
    """Refusal outcome when a PlanPatch cannot be applied to a plan."""

    def __init__(
        self,
        reason: RefusalReason,
        detail: str,
    ) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _parse_pointer(path: str) -> list[str]:
    """Decode a JSON Pointer path into reference segments."""
    if not path.startswith("/"):
        raise PlanPatchRefused("unknown_path", f"path '{path}' must begin with '/'")
    raw_segments = path.split("/")[1:]
    if not raw_segments or all(s == "" for s in raw_segments):
        raise PlanPatchRefused("unknown_path", f"path '{path}' specifies no target property")
    return [s.replace("~1", "/").replace("~0", "~") for s in raw_segments]


def _parse_list_index(segment: str, path: str) -> int:
    """Parse an array index from a pointer segment."""
    try:
        return int(segment)
    except ValueError:
        raise PlanPatchRefused("unknown_path", f"invalid array index '{segment}'") from None


def _resolve_parent_container(payload: Any, segments: list[str], path: str) -> Any:
    """Traverse pointer segments to the parent container."""
    current = payload
    for segment in segments[:-1]:
        if isinstance(current, dict):
            if segment not in current:
                raise PlanPatchRefused("unknown_path", f"property '{segment}' not found")
            current = current[segment]
        elif isinstance(current, list):
            idx = _parse_list_index(segment, path)
            if idx < 0 or idx >= len(current):
                raise PlanPatchRefused("index_out_of_range", f"index {idx} out of range")
            current = current[idx]
        else:
            raise PlanPatchRefused("unknown_path", f"cannot index into primitive at '{segment}'")
    return current


def _apply_add(container: Any, key: str, value: Any, path: str) -> None:
    """Apply an add operation to an object or array."""
    if isinstance(container, dict):
        container[key] = value
    elif isinstance(container, list):
        if key == "-":
            container.append(value)
        else:
            idx = _parse_list_index(key, path)
            if idx < 0 or idx > len(container):
                raise PlanPatchRefused("index_out_of_range", f"index {idx} out of range for add")
            container.insert(idx, value)
    else:
        raise PlanPatchRefused("unknown_path", f"cannot add to non-container at '{key}'")


def _apply_remove(container: Any, key: str, path: str) -> None:
    """Apply a remove operation to an object or array."""
    if isinstance(container, dict):
        if key not in container:
            raise PlanPatchRefused("unknown_path", f"property '{key}' not found for remove")
        del container[key]
    elif isinstance(container, list):
        idx = _parse_list_index(key, path)
        if idx < 0 or idx >= len(container):
            raise PlanPatchRefused("index_out_of_range", f"index {idx} out of range for remove")
        container.pop(idx)
    else:
        raise PlanPatchRefused("unknown_path", f"cannot remove from non-container at '{key}'")


def _apply_replace(container: Any, key: str, value: Any, path: str) -> None:
    """Apply a replace operation to an object or array."""
    if isinstance(container, dict):
        if key not in container:
            raise PlanPatchRefused("unknown_path", f"property '{key}' not found for replace")
        container[key] = value
    elif isinstance(container, list):
        idx = _parse_list_index(key, path)
        if idx < 0 or idx >= len(container):
            raise PlanPatchRefused("index_out_of_range", f"index {idx} out of range for replace")
        container[idx] = value
    else:
        raise PlanPatchRefused("unknown_path", f"cannot replace in non-container at '{key}'")


def _apply_single_op(payload: Any, op: PatchOp) -> None:
    """Apply a single patch operation to a payload dict tree."""
    segments = _parse_pointer(op.path)
    parent = _resolve_parent_container(payload, segments, op.path)
    target_key = segments[-1]

    if op.op == "add":
        _apply_add(parent, target_key, op.value, op.path)
    elif op.op == "remove":
        _apply_remove(parent, target_key, op.path)
    elif op.op == "replace":
        _apply_replace(parent, target_key, op.value, op.path)


def apply_plan_patch(
    plan: BusinessQueryPlan,
    ops: Sequence[PatchOp] | PlanPatch,
) -> BusinessQueryPlan:
    """Apply an ordered sequence of patch operations to a business query plan.

    Pure function: the input plan is never mutated. Operations are applied in
    order on a deep copy. The resulting dictionary is validated through
    BusinessQueryPlan.model_validate. Failures raise PlanPatchRefused.
    """
    operation_sequence: Sequence[PatchOp] = ops.ops if isinstance(ops, PlanPatch) else ops
    payload: Any = json.loads(plan.model_dump_json())

    for op in operation_sequence:
        _apply_single_op(payload, op)

    try:
        return BusinessQueryPlan.model_validate(payload)
    except (ValidationError, ValueError, TypeError) as exc:
        raise PlanPatchRefused("invalid_plan", str(exc)) from exc
