"""Orchestrate coverage map generation across all surfaces."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.engine import Engine

from app.coverage.annotations import load_annotations, merge
from app.coverage.billing_export import BillingExport
from app.coverage.diff import render_diff
from app.coverage.model import (
    ColumnBinding,
    CoverageMap,
    DataProfile,
    DbLabel,
    FieldChange,
    GatesLedger,
    GeneratedFrom,
    SurfaceRow,
)
from app.coverage.render_markdown import render_coverage_map, render_ledger, render_profile
from app.coverage.schema_inventory import SchemaInventory, database_name, inventory_schema
from app.coverage.surfaces.page_context import page_context_rows
from app.coverage.surfaces.record_tool import record_tool_rows
from app.coverage.surfaces.screens import screen_rows
from app.coverage.surfaces.sql_bundle import sql_bundle_rows
from app.coverage.view_lineage import lineage_for_views
from app.policy.manifest_loader import Manifest
from app.rag.page_context import registered_profile_keys

_HASH_KEYS = ("billing_export_hash", "bundle_hash", "db_schema_hash", "manifest_hash")


@dataclass(frozen=True, kw_only=True)
class GenerateInputs:
    """Inputs required to generate a complete surface coverage map."""

    engine: Engine
    manifest: Manifest
    manifest_hash: str
    bundle: Any
    billing_export: BillingExport | None = None
    annotations_path: Path | None = None
    billing_export_hash: str | None = None
    db_label: DbLabel | None = None


@dataclass(frozen=True, kw_only=True)
class GenerateResult:
    """A coverage map and the schema inventory its rows were resolved against."""

    coverage_map: CoverageMap
    inventory: SchemaInventory


def _rag_commit() -> str:
    """Extract short HEAD commit hash of the local repository."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def build_generated_from(
    inventory: SchemaInventory,
    manifest_hash: str,
    bundle_hash: str,
    *,
    billing_export_hash: str | None = None,
    billing_commit: str | None = None,
    db_label: DbLabel | None = None,
    db_name: str | None = None,
) -> GeneratedFrom:
    """Build a provenance record containing input artifact hashes and git commits."""
    return GeneratedFrom(
        db_schema_hash=inventory.schema_hash,
        manifest_hash=manifest_hash,
        bundle_hash=bundle_hash,
        billing_commit=billing_commit,
        billing_export_hash=billing_export_hash,
        rag_commit=_rag_commit(),
        db_label=db_label,
        db_name=db_name,
        generated_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def unmapped_tables(inventory: SchemaInventory, rows: list[SurfaceRow]) -> list[str]:
    """Identify all base table names in inventory.tables not referenced in rows."""
    bound = {b.table for r in rows for b in r.bindings if isinstance(b, ColumnBinding)}
    return sorted(t for t in inventory.tables if t not in bound)


def check_drift(committed: CoverageMap | None, fresh: GeneratedFrom) -> list[str]:
    """Return list of changed hash keys between committed map and fresh metadata."""
    if committed is None:
        return list(_HASH_KEYS)
    before = committed.generated_from.model_dump()
    after = fresh.model_dump()
    return [k for k in _HASH_KEYS if before.get(k) != after.get(k)]


def conflicts(rows: list[SurfaceRow]) -> list[str]:
    """Identify conflicting base column bindings across surfaces for identical fields."""
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for r in rows:
        col = next(
            (f"{b.table}.{b.column}" for b in r.bindings if isinstance(b, ColumnBinding)),
            None,
        )
        if col:
            seen.setdefault((r.resource, r.field), {}).setdefault(r.surface, col)
    out: list[str] = []
    for (resource, field), by_surface in sorted(seen.items()):
        if len(set(by_surface.values())) > 1:
            out.append(
                f"{resource}.{field}: "
                + " vs ".join(f"{s}→{c}" for s, c in sorted(by_surface.items()))
            )
    return out


def generate(inputs: GenerateInputs) -> GenerateResult:
    """Generate coverage map across record tool, SQL bundle, screens, and page context."""
    if inputs.annotations_path is not None and not inputs.annotations_path.exists():
        raise FileNotFoundError(f"Annotations file not found: {inputs.annotations_path}")

    inventory = inventory_schema(inputs.engine)
    lineage = lineage_for_views(inventory.views)

    rows: list[SurfaceRow] = []
    rows.extend(record_tool_rows(inputs.manifest, lineage, inventory))
    rows.extend(sql_bundle_rows(inputs.bundle, lineage, inventory))

    billing_commit: str | None = None
    billing_export_hash: str | None = inputs.billing_export_hash
    if inputs.billing_export is not None:
        rows.extend(screen_rows(inputs.billing_export, inventory))
        rows.extend(page_context_rows(inputs.billing_export, registered_profile_keys(), inventory))
        billing_commit = inputs.billing_export.billing_commit

    bundle_hash = (
        getattr(inputs.bundle, "content_hash", None)
        or getattr(inputs.bundle, "bundle_hash", None)
        or getattr(inputs.bundle, "hash", "")
    )

    generated_from = build_generated_from(
        inventory,
        inputs.manifest_hash,
        bundle_hash,
        billing_export_hash=billing_export_hash,
        billing_commit=billing_commit,
        db_label=inputs.db_label,
        db_name=database_name(inputs.engine),
    )

    coverage_map = CoverageMap(
        generated_from=generated_from,
        rows=rows,
        unmapped_tables=unmapped_tables(inventory, rows),
        conflicts=conflicts(rows),
    )

    if inputs.annotations_path is not None:
        annotations = load_annotations(inputs.annotations_path)
        coverage_map = merge(coverage_map, annotations, inventory=inventory)

    return GenerateResult(coverage_map=coverage_map, inventory=inventory)


def write_outputs(
    out_dir: Path,
    coverage_map: CoverageMap,
    markdown: str | None = None,
    diff_summary: str | None = None,
    *,
    ledger: GatesLedger | None = None,
    changes: list[FieldChange] | None = None,
    profile: DataProfile | None = None,
    snapshot_date: str | None = None,
    inventory: SchemaInventory | None = None,
) -> list[Path]:
    """Write all coverage artifacts to out_dir.

    When ledger and snapshot_date are provided, also writes gates-ledger files and diff.md.
    When profile is provided, writes data-profile files.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    # coverage-map.json
    json_path = out_dir / "coverage-map.json"
    json_path.write_text(coverage_map.model_dump_json(indent=2) + "\n", encoding="utf-8")
    written.append(json_path)

    # coverage-map.md
    if markdown is not None:
        md_content = markdown
    elif ledger is not None:
        # Full wiring: render using render_coverage_map with inventory
        md_content = render_coverage_map(coverage_map, inventory=inventory)
    else:
        md_content = None
    if md_content is not None:
        md_path = out_dir / "coverage-map.md"
        md_path.write_text(md_content, encoding="utf-8")
        written.append(md_path)

    if ledger is not None and snapshot_date is not None:
        # gates-ledger.json
        ledger_json_path = out_dir / "gates-ledger.json"
        ledger_json_path.write_text(ledger.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(ledger_json_path)

        # gates-ledger.md
        ledger_md_path = out_dir / "gates-ledger.md"
        ledger_md_path.write_text(render_ledger(ledger), encoding="utf-8")
        written.append(ledger_md_path)

        # YYYY-MM-DD-gates-ledger.json snapshot
        snapshot_path = out_dir / f"{snapshot_date}-gates-ledger.json"
        snapshot_path.write_text(ledger.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(snapshot_path)

        # diff.md
        diff_changes = changes if changes is not None else []
        diff_path = out_dir / "diff.md"
        diff_path.write_text(render_diff(diff_changes), encoding="utf-8")
        written.append(diff_path)
    elif diff_summary is not None:
        diff_path = out_dir / "diff.md"
        diff_path.write_text(diff_summary, encoding="utf-8")
        written.append(diff_path)

    if profile is not None:
        profile_json_path = out_dir / "data-profile.json"
        profile_json_path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(profile_json_path)

        profile_md_path = out_dir / "data-profile.md"
        profile_md_path.write_text(render_profile(profile), encoding="utf-8")
        written.append(profile_md_path)

    return written


def load_committed(path: Path) -> CoverageMap | None:
    """Return the committed map, or None when no file is there.

    A file that is present but unreadable raises: an unreadable committed map
    must not read as "no previous map", which would report drift and an empty diff.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as err:
        raise ValueError(f"committed map unreadable: {path}: {err}") from err
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError(f"committed map unreadable: {path}: not a schema_version 1 object")
    try:
        return CoverageMap.model_validate(raw)
    except ValidationError as err:
        raise ValueError(f"committed map unreadable: {path}: {err}") from err


def file_sha256(path: Path) -> str:
    """Compute sha256 digest of a file prefixed with sha256:."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
