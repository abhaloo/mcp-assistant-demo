"""
Three deterministic business-record tools (Ask AI context/access plan, Phase
5 — task A5). Typed, thin wrappers over ``PolicyScopedRecordExecutor``
(``app/policy/record_executor.py``) — no LLM, no free-form SQL, no ask-flow
wiring. Feature-off: nothing in ``app/services/`` or ``app/api/`` calls these
yet (see task-A5-brief.md, R4 — integration into answering is a later task).

Exactly three tools, per the binding design:
  - ``search_business_records`` — free-text query (matched against a
    resource's displayed, non-identifier fields) + optional structured
    filters. Requires the ``search`` action.
  - ``list_business_records`` — structured filters + optional manifest-
    declared sort, no free text. Requires the ``read`` action.
  - ``get_business_records`` — direct lookup by one or more canonical record
    ids. Requires the ``read`` action.

Inputs are strict Pydantic models (``extra="forbid"``): ``resource_type`` is
typed to the same ten-value registry ``ResourceType`` literal
``app/models/record_context.py`` already defines (task A3) — reusing it
here means a resource_type outside the known registry is rejected by the
type system before it ever reaches the executor's own manifest/snapshot
checks, not just by that later check.

``field_set`` (task A6, default ``"summary"`` — least-privilege by default)
threads straight through to ``PolicyScopedRecordExecutor``: it does the
authorization check (the requested set must be BOTH manifest-declared for
the resource AND granted to the principal) and the output projection (only
that set's ``field_set_fields`` are ever selected/returned) — see
``app/policy/record_executor.py``'s ``_resource_and_table``/``_select``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import Principal
from app.models.record_context import ResourceType
from app.policy.record_executor import (
    _DEFAULT_FIELD_SET,
    RecordGroup,
    RecordRow,
    build_record_executor,
)
from app.policy.record_query import (
    FilterClause,
    SortClause,
    legacy_filter_normalization_count,
    normalize_legacy_filters,
    normalize_legacy_sort,
)
from app.resources import ProcessResources
from app.telemetry.metrics import record_legacy_filter_normalization

_MAX_LIMIT = 50
_MAX_GET_IDS = 20
_MAX_GROUPS = 20


def _normalize_filters_with_telemetry(value: object) -> object:
    normalized = normalize_legacy_filters(value)
    record_legacy_filter_normalization(clauses=legacy_filter_normalization_count(value, normalized))
    return normalized


class SearchBusinessRecordsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: ResourceType
    query: str = Field(min_length=1, max_length=256)
    filters: list[FilterClause] = Field(default_factory=list, max_length=8)
    field_set: str = _DEFAULT_FIELD_SET
    limit: int = Field(default=10, ge=1, le=_MAX_LIMIT)

    @field_validator("filters", mode="before")
    @classmethod
    def _normalize_legacy_filters(cls, value: object) -> object:
        return _normalize_filters_with_telemetry(value)


class ListBusinessRecordsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: ResourceType
    filters: list[FilterClause] = Field(default_factory=list, max_length=8)
    sort: SortClause | None = None
    field_set: str = _DEFAULT_FIELD_SET
    limit: int = Field(default=20, ge=1, le=_MAX_LIMIT)

    @field_validator("filters", mode="before")
    @classmethod
    def _normalize_legacy_filters(cls, value: object) -> object:
        return _normalize_filters_with_telemetry(value)

    @field_validator("sort", mode="before")
    @classmethod
    def _normalize_legacy_sort(cls, value: object) -> object:
        return normalize_legacy_sort(value)


class GetBusinessRecordsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: ResourceType
    record_ids: list[int] = Field(min_length=1, max_length=_MAX_GET_IDS)
    field_set: str = _DEFAULT_FIELD_SET


class CountBusinessRecordsInput(BaseModel):
    """Feature-gated, scoped aggregate input; no free-form expressions."""

    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: ResourceType
    filters: list[FilterClause] = Field(default_factory=list, max_length=8)
    field_set: str = _DEFAULT_FIELD_SET

    @field_validator("filters", mode="before")
    @classmethod
    def _normalize_legacy_filters(cls, value: object) -> object:
        return _normalize_filters_with_telemetry(value)


class GroupBusinessRecordsInput(BaseModel):
    """Feature-gated bounded grouping input for count/group_count/top_groups."""

    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: ResourceType
    group_by: str = Field(min_length=1, max_length=64)
    filters: list[FilterClause] = Field(default_factory=list, max_length=8)
    field_set: str = _DEFAULT_FIELD_SET
    limit: int = Field(default=10, ge=1, le=_MAX_GROUPS)

    @field_validator("filters", mode="before")
    @classmethod
    def _normalize_legacy_filters(cls, value: object) -> object:
        return _normalize_filters_with_telemetry(value)


class BusinessRecordsResult(BaseModel):
    """Typed identities captured BEFORE any LLM formatting — the provenance
    seed a later ledger task consumes (this module does not build that
    ledger; it only returns this structured shape)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    records: list[RecordRow]


class RecordCountResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    count: int = Field(ge=0)


class RecordGroupsResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    groups: list[RecordGroup] = Field(max_length=_MAX_GROUPS)


def search_business_records(
    principal: Principal | None,
    params: SearchBusinessRecordsInput,
    *,
    resources: ProcessResources,
) -> BusinessRecordsResult:
    executor = build_record_executor(principal, resources=resources)
    rows = executor.search(
        params.resource_type,
        params.query,
        params.filters,
        field_set=params.field_set,
        limit=params.limit,
    )
    return BusinessRecordsResult(records=rows)


def list_business_records(
    principal: Principal | None,
    params: ListBusinessRecordsInput,
    *,
    resources: ProcessResources,
) -> BusinessRecordsResult:
    executor = build_record_executor(principal, resources=resources)
    rows = executor.list(
        params.resource_type,
        params.filters,
        sort=params.sort,
        field_set=params.field_set,
        limit=params.limit,
    )
    return BusinessRecordsResult(records=rows)


def get_business_records(
    principal: Principal | None,
    params: GetBusinessRecordsInput,
    *,
    resources: ProcessResources,
) -> BusinessRecordsResult:
    executor = build_record_executor(principal, resources=resources)
    rows = executor.get(params.resource_type, params.record_ids, field_set=params.field_set)
    return BusinessRecordsResult(records=rows)


def count_business_records(
    principal: Principal | None,
    params: CountBusinessRecordsInput,
    *,
    resources: ProcessResources,
) -> RecordCountResult:
    executor = build_record_executor(principal, resources=resources)
    return RecordCountResult(
        count=executor.count(params.resource_type, params.filters, field_set=params.field_set)
    )


def group_business_records(
    principal: Principal | None,
    params: GroupBusinessRecordsInput,
    *,
    resources: ProcessResources,
) -> RecordGroupsResult:
    executor = build_record_executor(principal, resources=resources)
    return RecordGroupsResult(
        groups=executor.group_count(
            params.resource_type,
            params.group_by,
            params.filters,
            field_set=params.field_set,
            limit=params.limit,
        )
    )
