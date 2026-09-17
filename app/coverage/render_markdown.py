"""Markdown views of the coverage map, the gates ledger, and the data profile."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.coverage.model import (
    ColumnBinding,
    CoverageMap,
    DataProfile,
    GatesLedger,
    GeneratedFrom,
    UnresolvedBinding,
    pivot_by_column,
)

if TYPE_CHECKING:
    from app.coverage.schema_inventory import SchemaInventory

SURFACES: tuple[str, ...] = ("screen", "record_tool", "sql_bundle", "page_context")


def _esc(value: object) -> str:
    """Escape pipe characters and collapse newlines for markdown tables."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    """Render a markdown table with headers and data rows."""
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_esc(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def generated_from_block(gf: GeneratedFrom) -> str:
    """Render provenance metadata into a key-value markdown table."""
    return md_table(["key", "value"], [[k, f"`{v}`"] for k, v in gf.model_dump().items()])


def resource_tables(coverage_map: CoverageMap) -> dict[str, set[str]]:
    """Collect the base tables bound by each resource's coverage rows."""
    out: dict[str, set[str]] = {}
    for row in coverage_map.rows:
        for b in row.bindings:
            if isinstance(b, ColumnBinding):
                out.setdefault(row.resource, set()).add(b.table)
    return out


def render_coverage_map(
    coverage_map: CoverageMap,
    inventory: SchemaInventory | None = None,
) -> str:
    """Render the complete coverage map as markdown with summary stats and per-resource tables."""
    pivot = pivot_by_column(coverage_map.rows)
    by_resource: dict[str, set[str]] = {}
    for row in coverage_map.rows:
        for b in row.bindings:
            if isinstance(b, ColumnBinding):
                by_resource.setdefault(row.resource, set()).add(f"{b.table}.{b.column}")

    if inventory is not None:
        res_tables = resource_tables(coverage_map)
        for resource, tables in res_tables.items():
            for table in tables:
                for col in inventory.tables.get(table, []):
                    by_resource.setdefault(resource, set()).add(f"{table}.{col.column}")

    parts = [
        "# AI record-field coverage map",
        "",
        "**Generated from:**",
        "",
        generated_from_block(coverage_map.generated_from),
        "",
    ]

    all_keys = {k for keys in by_resource.values() for k in keys}
    total_columns = len(all_keys)
    fully_covered = sum(
        1 for k in all_keys if k in pivot and len(pivot[k].surfaces) == len(SURFACES)
    )
    partially_covered = sum(
        1 for k in all_keys if k in pivot and 0 < len(pivot[k].surfaces) < len(SURFACES)
    )
    unmapped = sum(1 for k in all_keys if k not in pivot or len(pivot[k].surfaces) == 0)

    summary = []
    for resource in sorted(by_resource):
        keys = by_resource[resource]
        counts = [sum(1 for k in keys if k in pivot and s in pivot[k].surfaces) for s in SURFACES]
        summary.append([f"`{resource}`", len(keys), *counts])

    parts += [
        "## Summary",
        "",
        md_table(["resource", "bound columns", *SURFACES], summary),
        "",
        f"- Total columns: {total_columns}",
        f"- Fully covered: {fully_covered}",
        f"- Partially covered: {partially_covered}",
        f"- Unmapped: {unmapped}",
        "",
    ]

    for resource in sorted(by_resource):
        rows = []
        for key in sorted(by_resource[resource]):
            cov = pivot.get(key)
            if cov is not None:
                marks = ["yes" if s in cov.surfaces else "no" for s in SURFACES]
                members_str = ", ".join(cov.members)
            else:
                marks = ["no" for _ in SURFACES]
                members_str = ""
            rows.append([f"`{key}`", *marks, members_str, coverage_map.annotations.get(key, "")])
        parts += [
            f"## `{resource}`",
            "",
            md_table(["column", *SURFACES, "members", "annotation"], rows),
            "",
        ]
        note = coverage_map.annotations.get(f"{resource}/*")
        if note:
            parts += [f"**Findings:** {note}", ""]

    unresolved = [
        r for r in coverage_map.rows if r.bindings and isinstance(r.bindings[0], UnresolvedBinding)
    ]
    if unresolved:
        parts += [
            "## Unresolved rows",
            "",
            md_table(
                ["member", "reason", "evidence"],
                [[r.member, r.bindings[0].reason, r.evidence] for r in unresolved],
            ),
            "",
        ]

    parts += [
        "## Unmapped tables",
        "",
        ", ".join(f"`{t}`" for t in coverage_map.unmapped_tables) or "none",
        "",
    ]
    parts += [
        "## Conflicts",
        "",
        "\n".join(f"- {c}" for c in coverage_map.conflicts) or "none",
        "",
    ]
    if coverage_map.stale_annotations:
        parts += [
            "## Stale annotations",
            "",
            "\n".join(f"- `{k}`" for k in coverage_map.stale_annotations),
            "",
        ]

    return "\n".join(parts)


def render_ledger(ledger: GatesLedger) -> str:
    """Render the detail-gates ledger including resource scoreboard and gate rows."""
    parts = [
        "# Detail-gates ledger",
        "",
        generated_from_block(ledger.generated_from),
        "",
        "## Scoreboard",
        "",
    ]
    parts.append(
        md_table(
            ["resource", "rows", "pass all 5", "fields"],
            [
                [
                    f"`{s.resource}`",
                    s.total_rows,
                    s.pass_all_five,
                    ", ".join(s.pass_all_five_fields),
                ]
                for s in ledger.scoreboard
            ],
        )
    )
    parts += [
        "",
        "## Details",
        "",
        md_table(
            [
                "resource",
                "field",
                "kind",
                "source",
                "permission",
                "projection",
                "signed_bundle",
                "oracle_and_browser",
                "all 5",
            ],
            [
                [
                    r.resource,
                    r.field,
                    r.kind,
                    r.source,
                    r.permission,
                    r.projection,
                    r.signed_bundle,
                    r.oracle_and_browser,
                    "yes" if r.passes_all_five else "no",
                ]
                for r in ledger.rows
            ],
        ),
        "",
    ]
    return "\n".join(parts)


def render_profile(profile: DataProfile) -> str:
    """Render the database column profile into a markdown table."""
    rows = [
        [
            f"`{c.table}.{c.column}`",
            c.data_type,
            c.row_count,
            c.null_count,
            c.distinct_count,
            c.numeric_pure_count,
            c.max_length,
            f"{c.min_year}–{c.max_year}" if c.min_year else "",
            c.orphan_count,
            c.profile_note or "",
        ]
        for c in profile.columns
    ]
    return "\n".join(
        [
            "# Data profile",
            "",
            generated_from_block(profile.generated_from),
            "",
            md_table(
                [
                    "column",
                    "type",
                    "rows",
                    "nulls",
                    "distinct",
                    "numeric-pure",
                    "max len",
                    "years",
                    "orphans",
                    "note",
                ],
                rows,
            ),
            "",
        ]
    )
