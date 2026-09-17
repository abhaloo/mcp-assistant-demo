"""Pydantic schemas and models for business-definition bundles."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.business_query.definitions.affordances import _affordance_violations
from app.business_query.definitions.view_columns import (
    _SQL_IDENTIFIER_RE,
    DETAIL_VIEW_COLUMNS,
)

_MAX_ACCEPTED_BUNDLES = 2
_BUNDLE_HASH_PATTERN = r"^sha256:[0-9a-fA-F]{64}$"


class InvalidBundleIndexError(ValueError):
    """Malformed index, missing/tampered bundle, or unaccepted hash."""


class BundleValidationError(ValueError):
    """Batched schema / expression / compatibility violations."""


class BundleSelectionError(ValueError):
    """No accepted bundle is compatible with the given manifest hash."""


class ScopeColumns(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    entity: str | None
    department: str | None


class ResourceBinding(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    projection_view: str
    primary_key: str
    reference_dimension: str | None = None
    department_scope_mode: Literal["none", "filter_when_present", "required_match"]
    record_predicates: dict[str, str | int | float | bool]
    scope_columns: ScopeColumns


class DetailDefinition(BaseModel):
    """Signed logical-to-physical mapping for one typed detail family."""

    model_config = ConfigDict(strict=True, extra="forbid")

    family_key: str
    revision_hash: Annotated[str, Field(pattern=_BUNDLE_HASH_PATTERN)]
    logical_source: str
    physical_source: str
    owner_resource: str
    owner_column: str
    value_column: str
    value_kind: Literal[
        "text",
        "string",
        "integer",
        "decimal",
        "boolean",
        "date",
        "datetime",
        "enum",
        "money",
        "quantity",
    ]
    cardinality: Literal["one_to_one", "one_to_many"]
    scope_columns: ScopeColumns
    family_aliases: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    value_mapping: dict[str, str | int | float | bool] = Field(default_factory=dict)
    required_permissions: list[str] = Field(default_factory=list)
    is_current: bool = True
    uses_union: Literal[False] = False

    @field_validator("physical_source")
    @classmethod
    def _physical_source_is_curated(cls, value: str) -> str:
        if not _SQL_IDENTIFIER_RE.fullmatch(value) or "union" in value.lower():
            raise ValueError("physical_source must be a safe non-UNION detail fact identifier")
        return value

    @model_validator(mode="after")
    def _columns_exist_on_curated_source(self) -> DetailDefinition:
        for label, column in (
            ("owner_column", self.owner_column),
            ("value_column", self.value_column),
            ("scope column", self.scope_columns.entity),
            ("scope column", self.scope_columns.department),
        ):
            if column is not None and not _SQL_IDENTIFIER_RE.fullmatch(column):
                raise ValueError(f"{label} {column!r} is not a safe source identifier")
        if "union" in self.logical_source.lower():
            raise ValueError("logical_source must not name a UNION")
        return self


class DetailSource(BaseModel):
    """Signed physical source metadata paired with one detail family."""

    model_config = ConfigDict(strict=True, extra="forbid")

    family_key: str
    projection_view: str
    owner_resource: str
    owner_column: str
    grain: str = Field(min_length=1)
    scope_columns: ScopeColumns
    typed_value_column: str
    display_value_column: str
    columns: list[str] = Field(default_factory=list)
    uses_union: Literal[False] = False

    @field_validator("projection_view")
    @classmethod
    def _projection_view_is_curated(cls, value: str) -> str:
        if not _SQL_IDENTIFIER_RE.fullmatch(value) or "union" in value.lower():
            raise ValueError("projection_view must be a safe non-UNION detail fact identifier")
        return value

    @model_validator(mode="after")
    def _columns_exist_on_curated_source(self) -> DetailSource:
        identifiers = {
            self.owner_column,
            self.typed_value_column,
            self.display_value_column,
            *(column for column in self.scope_columns.model_dump().values() if column is not None),
            *self.columns,
        }
        if any(not _SQL_IDENTIFIER_RE.fullmatch(column) for column in identifiers):
            raise ValueError("detail source columns must be safe SQL identifiers")
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("detail source columns must be unique")
        return self


def detail_source_columns(source: DetailSource) -> frozenset[str]:
    """Return the signed source columns, with a legacy B1 compatibility path."""
    if source.columns:
        return frozenset(source.columns)
    return DETAIL_VIEW_COLUMNS.get(source.projection_view, frozenset())


class MeasureDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    owning_resource: str
    sql_expression: str
    agg_type: Literal["sum", "avg", "min", "max", "count", "count_distinct", "derived"]
    filter_sql: str | None
    description: str
    format: Literal["currency", "number", "percent"] | None
    time_dimension: str | None
    currency_dimension: str | None = None
    allowed_time_axes: list[str] = Field(default_factory=list)
    snapshot: bool = False
    relative_to_business_date: bool = False
    overdue_after_days: int | None = None
    capability_state: Literal["enabled", "decision_pending", "unsupported"]
    value_kind: (
        Literal["string", "integer", "decimal", "currency", "date", "datetime", "boolean"] | None
    ) = None
    expression_kind: Literal["ratio", "share"] | None = None
    derived_from: list[str] = Field(default_factory=list)

    def counts_rows(self) -> bool:
        return self.agg_type in {"count", "count_distinct"}


class DimensionDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    owning_resource: str
    sql_expression: str
    type: Literal["string", "number", "time", "boolean"]
    is_primary_key: bool
    description: str
    allowed_values: list[str] = Field(default_factory=list)
    value_kind: (
        Literal["string", "integer", "decimal", "currency", "date", "datetime", "boolean"] | None
    ) = None
    direct_relationships: list[str] = Field(default_factory=list)
    resolvable_as: Literal["customer", "product", "department"] | None = None


class JoinDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    from_resource: str
    to_resource: str
    relationship: Literal["one_to_one", "one_to_many", "many_to_one"]
    on_sql: str
    optional: bool = False


class BucketSetDefinition(BaseModel):
    """Two shapes share this definition, discriminated by which list is filled.

    Threshold: ``dimension`` + ordered ``buckets`` of (label, upper bound) —
    the aging shape. Categorical: ``bucket_predicates`` of (label,
    predicate_sql), one bucket per mutually-exclusive predicate — the
    payment-status shape. Exactly one list may be non-empty.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    owning_resource: str
    dimension: str = ""
    buckets: list[tuple[str, int | None]] = Field(default_factory=list)
    applies_filter_sql: str
    description: str
    relative_to_business_date: bool = False
    bucket_predicates: list[tuple[str, str]] = Field(default_factory=list)

    @field_validator("buckets", "bucket_predicates", mode="before")
    @classmethod
    def _coerce_bucket_pairs(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        coerced: list[object] = []
        for pair in value:
            if isinstance(pair, list) and len(pair) == 2:
                coerced.append((pair[0], pair[1]))
            else:
                coerced.append(pair)
        return coerced

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> BucketSetDefinition:
        if bool(self.buckets) == bool(self.bucket_predicates):
            raise ValueError(
                f"bucket_set {self.name!r}: exactly one of buckets or "
                "bucket_predicates must be non-empty"
            )
        if self.buckets and not self.dimension:
            raise ValueError(f"bucket_set {self.name!r}: threshold buckets require a dimension")
        if self.bucket_predicates and (self.dimension or self.relative_to_business_date):
            raise ValueError(
                f"bucket_set {self.name!r}: categorical buckets take no dimension "
                "and no business-date anchor"
            )
        return self


class SegmentDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    owning_resource: str
    predicate_sql: str
    description: str
    capability_state: Literal["enabled", "decision_pending", "unsupported"]


class CapabilityEntry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    kind: Literal["measure", "dimension", "bucket_set", "segment"]
    resolves_to: str
    title: str
    description: str
    example_questions: list[str]
    required_filters: list[str]
    required_permissions: list[str]
    capability_state: Literal["enabled", "decision_pending", "unsupported"]
    grantable: bool
    meta: dict[str, Any]


class DetailFamilyDefinition(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    owning_resource: str
    description: str = ""
    value_kind: (
        Literal["string", "integer", "decimal", "currency", "date", "datetime", "boolean", "json"]
        | None
    ) = None


class DefinitionBundle(BaseModel):
    """Typed mirror of Billing's ExportAiBusinessDefinitions payload."""

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal["business-definitions/v1"]
    compatibility_epoch: int = Field(default=1, ge=1)
    content_hash: Annotated[str, Field(pattern=_BUNDLE_HASH_PATTERN)]
    business_timezone: str
    compatible_policy_manifest_hashes: list[Annotated[str, Field(pattern=_BUNDLE_HASH_PATTERN)]]
    resources: list[ResourceBinding]
    measures: list[MeasureDefinition]
    dimensions: list[DimensionDefinition]
    joins: list[JoinDefinition]
    bucket_sets: list[BucketSetDefinition]
    segments: list[SegmentDefinition] = Field(default_factory=list)
    capabilities: list[CapabilityEntry]
    safe_combinations: list[list[str]] = Field(default_factory=list)
    detail_definitions: list[DetailDefinition] = Field(default_factory=list)
    detail_sources: list[DetailSource] = Field(default_factory=list)
    detail_families: list[DetailFamilyDefinition] = Field(default_factory=list)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version_is_exact_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("schema_version must be the string 'business-definitions/v1'")
        return value

    @model_validator(mode="after")
    def _affordances_reference_declared_definitions(self) -> DefinitionBundle:
        violations = _affordance_violations(self)
        if violations:
            raise ValueError("; ".join(violations))

        definitions: dict[str, list[DetailDefinition]] = {}
        resource_names = {resource.name for resource in self.resources}
        dimensions_by_name = {dimension.name: dimension for dimension in self.dimensions}
        for resource in self.resources:
            if resource.reference_dimension is None:
                continue
            dimension = dimensions_by_name.get(resource.reference_dimension)
            if dimension is None or dimension.owning_resource != resource.name:
                raise ValueError(
                    f"resource {resource.name!r} reference_dimension must name "
                    "one of its declared dimensions"
                )
        for detail in self.detail_definitions:
            if detail.owner_resource not in resource_names:
                raise ValueError(
                    f"detail definition {detail.family_key!r} owner_resource "
                    f"{detail.owner_resource!r} is not declared"
                )
            family = definitions.setdefault(detail.family_key, [])
            if any(existing.revision_hash == detail.revision_hash for existing in family):
                raise ValueError(
                    f"duplicate detail definition revision {detail.family_key!r}/"
                    f"{detail.revision_hash!r}"
                )
            family.append(detail)
        for family_key, revisions in definitions.items():
            if len(revisions) > 1 and sum(revision.is_current for revision in revisions) != 1:
                raise ValueError(
                    f"detail family {family_key!r} must declare exactly one current revision"
                )

        sources: dict[str, DetailSource] = {}
        for source in self.detail_sources:
            if source.family_key in sources:
                raise ValueError(f"duplicate detail source family_key {source.family_key!r}")
            sources[source.family_key] = source
            if source.owner_resource not in resource_names:
                raise ValueError(
                    f"detail source {source.family_key!r} owner_resource "
                    f"{source.owner_resource!r} is not declared"
                )
            revisions = definitions.get(source.family_key)
            if not revisions:
                raise ValueError(
                    f"detail source {source.family_key!r} has no matching detail definition"
                )
            columns = detail_source_columns(source)
            if not columns:
                raise ValueError(f"detail source {source.family_key!r} must declare source columns")
            for definition in revisions:
                if source.projection_view != definition.physical_source:
                    raise ValueError(
                        f"detail source {source.family_key!r} projection_view must match "
                        "physical_source"
                    )
                if source.owner_resource != definition.owner_resource:
                    raise ValueError(
                        f"detail source {source.family_key!r} owner_resource must match "
                        "detail definition"
                    )
                if source.owner_column != definition.owner_column:
                    raise ValueError(
                        f"detail source {source.family_key!r} owner_column must match "
                        "detail definition"
                    )
                if source.typed_value_column != definition.value_column:
                    raise ValueError(
                        f"detail source {source.family_key!r} typed_value_column must match "
                        "value_column"
                    )
                if source.scope_columns != definition.scope_columns:
                    raise ValueError(
                        f"detail source {source.family_key!r} scope_columns must match "
                        "detail definition"
                    )
            required_columns = {
                source.owner_column,
                source.typed_value_column,
                source.display_value_column,
                *(
                    column
                    for column in source.scope_columns.model_dump().values()
                    if column is not None
                ),
            }
            if len(revisions) > 1:
                required_columns.add("revision_hash")
            missing = sorted(required_columns - columns)
            if missing:
                raise ValueError(
                    f"detail source {source.family_key!r} is missing columns: {', '.join(missing)}"
                )

        missing_sources = sorted(set(definitions) - set(sources))
        if missing_sources:
            raise ValueError(
                "detail definitions require matching signed sources: " + ", ".join(missing_sources)
            )
        return self


class BundleIndex(BaseModel):
    """``index.json`` — mirrors ``ManifestIndex`` (schema_version / current / accepted)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1]
    current: Annotated[str, Field(pattern=_BUNDLE_HASH_PATTERN)]
    accepted: list[Annotated[str, Field(pattern=_BUNDLE_HASH_PATTERN)]]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version_is_a_plain_int(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("schema_version must be exactly 1 (int)")
        return value

    @model_validator(mode="after")
    def _accepted_is_bounded_deduped_and_contains_current(self) -> BundleIndex:
        if not self.accepted:
            raise ValueError("accepted must name at least one bundle hash")
        if len(self.accepted) > _MAX_ACCEPTED_BUNDLES:
            raise ValueError(
                f"accepted must name at most {_MAX_ACCEPTED_BUNDLES} bundle hashes "
                f"(current + previous), got {len(self.accepted)}"
            )
        if len(set(self.accepted)) != len(self.accepted):
            raise ValueError("accepted must not contain duplicate hashes")
        if self.current not in self.accepted:
            raise ValueError("current must be a member of accepted")
        return self
