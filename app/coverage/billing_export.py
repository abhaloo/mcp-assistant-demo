"""Typed seam for the JSON that billing's ai:coverage-export writes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ExportRoute(_Strict):
    name: str
    uri: str
    controller: str
    middleware: list[str]
    root_variable: str
    root_model: str
    view_variables: dict[str, str] = Field(default_factory=dict)
    blades: list[str]

    @field_validator("view_variables", mode="before")
    @classmethod
    def _normalize_view_variables(cls, value: Any) -> Any:
        if isinstance(value, list) and not value:
            return {}
        return value


class ExportRelation(_Strict):
    name: str
    status: Literal["ok", "class_not_found", "error"]
    relation_type: str | None
    related_model: str | None
    foreign_key: str | None


class ExportModel(_Strict):
    class_name: str
    table: str
    casts: dict[str, str]
    appends: list[str]
    accessors: list[str]
    relations: list[ExportRelation]


class BladeRef(_Strict):
    line: int
    variable: str
    chain: list[str]
    is_method: bool


class LoopAlias(_Strict):
    alias: str
    variable: str
    chain: list[str]


class ExportBlade(_Strict):
    file: str
    root_variable: str
    root_model: str
    view_variables: dict[str, str] = Field(default_factory=dict)
    refs: list[BladeRef]
    loop_aliases: list[LoopAlias]
    can_gates: list[str]
    includes: list[str]
    livewire: list[str]

    @field_validator("view_variables", mode="before")
    @classmethod
    def _normalize_view_variables(cls, value: Any) -> Any:
        if isinstance(value, list) and not value:
            return {}
        return value


class ExportProfile(_Strict):
    profile: str
    resource_type: str
    class_name: str
    root_model: str
    fields: dict[str, list[str] | None]
    detail_extra_fields: list[str]
    field_caps: dict[str, int]


class BillingExport(_Strict):
    schema_version: Literal[1] = 1
    billing_commit: str
    generated_utc: str
    routes: list[ExportRoute]
    blades: list[ExportBlade]
    models: list[ExportModel]
    page_context_profiles: list[ExportProfile]

    def model_by_class(self) -> dict[str, ExportModel]:
        return {m.class_name: m for m in self.models}


class BillingExportSchemaError(ValueError):
    """The export does not have schema 1 or carries invalid keys."""


def load_billing_export(path: Path) -> BillingExport:
    """Read a billing export JSON file and validate it against schema 1."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError) as err:
        raise BillingExportSchemaError(str(err)) from err

    if not isinstance(raw, dict):
        raise BillingExportSchemaError(
            f"export payload must be a JSON object, got {type(raw).__name__}"
        )

    if raw.get("schema_version") != 1:
        version = raw.get("schema_version")
        raise BillingExportSchemaError(f"schema_version must be 1, got {version!r}")

    try:
        return BillingExport.model_validate(raw)
    except (ValidationError, KeyError) as err:
        raise BillingExportSchemaError(str(err)) from err
