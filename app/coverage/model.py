"""Typed shapes for the coverage map, the gates ledger, and the data profile."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Base model that forbids extra keys and enforces strict typing."""

    model_config = ConfigDict(strict=True, extra="forbid")


class ColumnBinding(_Strict):
    """Binding that resolves directly to a database table and column."""

    kind: Literal["column"] = "column"
    table: str
    column: str


class ComputedBinding(_Strict):
    """Binding derived from an expression or computation."""

    kind: Literal["computed"] = "computed"
    expression: str
    cite: str | None = None


class UnresolvedBinding(_Strict):
    """Binding that could not be mapped to a column or computation."""

    kind: Literal["unresolved"] = "unresolved"
    reason: str
    cite: str | None = None


Binding = Annotated[
    ColumnBinding | ComputedBinding | UnresolvedBinding,
    Field(discriminator="kind"),
]

Surface = Literal["screen", "record_tool", "sql_bundle", "page_context"]


class SurfaceRow(_Strict):
    """Single field mapping row for an exposed surface."""

    surface: Surface
    resource: str
    field: str
    member: str
    view: str | None = None
    bindings: list[Binding]
    permissions: list[str] = Field(default_factory=list)
    evidence: str


DbLabel = Literal["dev", "uat", "prod", "prod-clone", "prod-wire-eval"]


class GeneratedFrom(_Strict):
    """Provenance metadata recording schema hashes and source commits."""

    schema_version: int = 1
    db_schema_hash: str
    manifest_hash: str
    bundle_hash: str
    billing_commit: str | None = None
    billing_export_hash: str | None = None
    rag_commit: str
    db_label: DbLabel | None = None
    db_name: str | None = None
    generated_utc: str


class ColumnCoverage(_Strict):
    """Aggregated coverage across surfaces for a single database column."""

    table: str
    column: str
    surfaces: set[Surface]
    members: list[str]


class CoverageMap(_Strict):
    """Complete inventory of surface field mappings and database column coverage."""

    schema_version: int = 1
    generated_from: GeneratedFrom
    rows: list[SurfaceRow]
    unmapped_tables: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    annotations: dict[str, str] = Field(default_factory=dict)
    stale_annotations: list[str] = Field(default_factory=list)


GateKind = Literal[
    "measure",
    "dimension",
    "bucket_set",
    "detail_family",
    "record_field",
    "screen_or_page_context",
]


class GateRow(_Strict):
    """Single gate row representing five-gate verification for a field."""

    resource: str
    field: str
    kind: GateKind
    source: str
    permission: str
    projection: str
    signed_bundle: str
    oracle_and_browser: str
    passes_all_five: bool


class ScoreboardRow(_Strict):
    """Aggregate gate summary row per resource."""

    resource: str
    total_rows: int
    pass_all_five: int
    pass_all_five_fields: list[str]


class GatesLedger(_Strict):
    """Five-gate verification ledger and resource scoreboard."""

    schema_version: int = 1
    generated_from: GeneratedFrom
    rows: list[GateRow]
    scoreboard: list[ScoreboardRow]


class ColumnProfile(_Strict):
    """Statistical and population facts for a single database column."""

    table: str
    column: str
    data_type: str
    row_count: int
    null_count: int
    distinct_count: int | None = None
    numeric_pure_count: int | None = None
    max_length: int | None = None
    min_year: int | None = None
    max_year: int | None = None
    value_histogram: dict[str, int] | None = None
    histogram_suppressed: bool = False
    orphan_count: int | None = None
    profile_note: str | None = None


class DataProfile(_Strict):
    """Collection of database column profiles."""

    schema_version: int = 1
    generated_from: GeneratedFrom
    columns: list[ColumnProfile]


AuditRule = Literal[
    "value_missing_from_allowed",
    "allowed_values_collide",
    "allowed_value_absent_from_data",
    "enum_like_without_allowed_values",
    "always_null",
    "not_auditable",
]


class AuditFinding(_Strict):
    """One audit result for one bundle member."""

    member: str
    rule: AuditRule
    severity: Literal["gate", "advice"]
    values: list[str] = Field(default_factory=list)
    proposal: str | None = None
    reason: str | None = None


class BundleAudit(_Strict):
    """Bundle allowed_values checked against live projection-view values."""

    schema_version: int = 1
    generated_from: GeneratedFrom
    findings: list[AuditFinding]
    gate_count: int


class FieldChange(_Strict):
    """Change in a field mapping between two coverage map runs."""

    key: str
    before: str
    after: str


class OracleProof(_Strict):
    """Verification record comparing declared fields with proven test fields."""

    member: str
    declared: list[str]
    proven: list[str]


def pivot_by_column(rows: list[SurfaceRow]) -> dict[str, ColumnCoverage]:
    """Group rows by base column.

    Computed and unresolved bindings do not create a database column key.
    """
    out: dict[str, ColumnCoverage] = {}
    for row in rows:
        for binding in row.bindings:
            if not isinstance(binding, ColumnBinding):
                continue
            key = f"{binding.table}.{binding.column}"
            cov = out.setdefault(
                key,
                ColumnCoverage(
                    table=binding.table,
                    column=binding.column,
                    surfaces=set(),
                    members=[],
                ),
            )
            cov.surfaces.add(row.surface)
            if row.member not in cov.members:
                cov.members.append(row.member)
    for cov in out.values():
        cov.members.sort()
    return out
