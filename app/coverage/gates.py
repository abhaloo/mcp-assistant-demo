"""Five-gate verification ledger and scoreboard across surfaces."""

from __future__ import annotations

import re
from typing import Any

from app.business_query.definitions.schema import DefinitionBundle
from app.coverage.model import (
    ColumnBinding,
    ComputedBinding,
    CoverageMap,
    GateKind,
    GateRow,
    GatesLedger,
    GeneratedFrom,
    OracleProof,
    ScoreboardRow,
    SurfaceRow,
)

NONE = "NONE"
_VIEW_RE = re.compile(r"\b(ai_v[0-9]+_[a-z0-9_]+)\b")
_KIND_BY_PREFIX: dict[str, GateKind] = {
    "dimension": "dimension",
    "measure": "measure",
    "family": "detail_family",
    "field": "record_field",
    "screen": "screen_or_page_context",
    "page_context": "screen_or_page_context",
    "bucket_set": "bucket_set",
    "bucket": "bucket_set",
}


def _source(row: SurfaceRow) -> str:
    """Extract source representation from surface row bindings."""
    if not row.bindings:
        return NONE
    binding = row.bindings[0]
    if isinstance(binding, ColumnBinding):
        return f"{binding.table}.{binding.column}"
    if isinstance(binding, ComputedBinding):
        return f"computed:{binding.expression}"
    return f"unresolved:{binding.reason}"


def _oracle(member: str, oracles: dict[str, OracleProof]) -> str:
    """Format oracle verification status for a member."""
    proof = oracles.get(member)
    if proof is None or not proof.declared:
        return NONE
    if proof.proven:
        return f"proven:{','.join(proof.proven)}"
    return f"declared:{','.join(proof.declared)}"


def build_ledger(
    coverage_map: CoverageMap,
    bundle: DefinitionBundle | Any,
    oracles: dict[str, OracleProof],
    generated_from: GeneratedFrom,
) -> GatesLedger:
    """Build the five-gate verification ledger and resource scoreboard.

    Args:
        coverage_map: Mapped field inventory across surfaces.
        bundle: Canonical definition bundle.
        oracles: Oracle proof records by member identifier.
        generated_from: Provenance metadata.

    Returns:
        Populated GatesLedger with row evaluations and resource scoreboard.
    """
    signed: set[str] = set()
    if hasattr(bundle, "dimensions"):
        signed.update(f"dimension:{d.name}" for d in bundle.dimensions)
    if hasattr(bundle, "measures"):
        signed.update(f"measure:{m.name}" for m in bundle.measures)
    if hasattr(bundle, "detail_definitions"):
        signed.update(f"family:{d.family_key}" for d in bundle.detail_definitions)
    if hasattr(bundle, "bucket_sets"):
        signed.update(f"bucket_set:{b.name}" for b in bundle.bucket_sets)

    rows: list[GateRow] = []
    for row in coverage_map.rows:
        if row.surface not in ("record_tool", "sql_bundle"):
            continue

        view_match = _VIEW_RE.search(row.evidence)
        projection_cell = row.view if row.view else (view_match.group(1) if view_match else NONE)
        oracle_cell = _oracle(row.member, oracles)
        signed_bundle_cell = (
            NONE if row.surface == "record_tool" else (row.member if row.member in signed else NONE)
        )

        cells = {
            "source": _source(row),
            "permission": ", ".join(row.permissions) or NONE,
            "projection": projection_cell,
            "signed_bundle": signed_bundle_cell,
            "oracle_and_browser": oracle_cell,
        }

        passes = all(
            v != NONE and not v.startswith("unresolved") for v in cells.values()
        ) and oracle_cell.startswith("proven:")

        prefix = row.member.split(":")[0] if ":" in row.member else row.member
        kind = _KIND_BY_PREFIX.get(prefix, "record_field")

        rows.append(
            GateRow(
                resource=row.resource,
                field=row.field,
                kind=kind,
                passes_all_five=passes,
                **cells,
            )
        )

    scoreboard: list[ScoreboardRow] = []
    for resource in sorted({r.resource for r in rows}):
        matching_rows = [r for r in rows if r.resource == resource]
        passing_fields = [r.field for r in matching_rows if r.passes_all_five]
        scoreboard.append(
            ScoreboardRow(
                resource=resource,
                total_rows=len(matching_rows),
                pass_all_five=len(passing_fields),
                pass_all_five_fields=passing_fields,
            )
        )

    return GatesLedger(
        schema_version=1,
        generated_from=generated_from,
        rows=rows,
        scoreboard=scoreboard,
    )
