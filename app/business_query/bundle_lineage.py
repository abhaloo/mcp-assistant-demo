"""View-column → billing source lineage for PII register joins."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from app.business_query.definitions import VIEW_COLUMNS
from app.guardrails.pii_register import REGISTER, FieldPolicy, Treatment

if TYPE_CHECKING:
    from app.business_query.definitions import DefinitionBundle
    from app.business_query.plan import BusinessQueryPlan

_DEFAULT_PATH = Path(__file__).parent / "bundle_lineage.json"
_FORBIDDEN_TREATMENTS = frozenset({Treatment.REDACT, Treatment.SUPPRESS})

# A bundle member's sql_expression is not always a bare column name --
# COALESCE(customer_name, status) or SUM(credit - debit) reference more than
# one view column. Every identifier in the expression must be checked
# against lineage, not just the whole-string case. String literals are
# stripped first so a quoted value can never be mistaken for a column
# reference.
_STRING_LITERAL_RE = re.compile(r"'(?:[^'\\])*'")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class BundleLineageError(RuntimeError):
    """Malformed or incomplete lineage sidecar."""


@dataclass(frozen=True)
class LineageEntry:
    table: str | None = None
    column: str | None = None
    derived: bool = False
    # Only meaningful when derived=True. An explicit reason a column is
    # genuinely computed in THIS view even though the same column name is a
    # plain {table, column} mapping in another view -- required to silence
    # the derived/plain-mapping consistency check below.
    justification: str | None = None


@dataclass(frozen=True)
class ClassifiedColumn:
    member: str
    view: str
    view_column: str
    table: str
    column: str
    treatment: Treatment
    policy: FieldPolicy


def _parse_entry(raw: object, *, view: str, column: str) -> LineageEntry:
    if not isinstance(raw, dict):
        raise BundleLineageError(f"lineage entry for {view}.{column} must be an object")
    if raw.get("derived") is True:
        justification = raw.get("justification")
        if justification is not None and not isinstance(justification, str):
            raise BundleLineageError(
                f"lineage entry for {view}.{column}: justification must be a string"
            )
        return LineageEntry(derived=True, justification=justification)
    table = raw.get("table")
    col = raw.get("column")
    if not isinstance(table, str) or not table.strip():
        raise BundleLineageError(f"lineage entry for {view}.{column} missing table")
    if not isinstance(col, str) or not col.strip():
        raise BundleLineageError(f"lineage entry for {view}.{column} missing column")
    return LineageEntry(table=table.strip().lower(), column=col.strip().lower(), derived=False)


@lru_cache(maxsize=1)
def load_lineage(path: Path = _DEFAULT_PATH) -> dict[str, dict[str, LineageEntry]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BundleLineageError(f"bundle lineage file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BundleLineageError(f"bundle lineage is not valid JSON: {exc}") from exc

    views = raw.get("views")
    if not isinstance(views, dict):
        raise BundleLineageError("bundle lineage must contain a views object")

    lineage: dict[str, dict[str, LineageEntry]] = {}
    for view_name, columns in views.items():
        if not isinstance(view_name, str) or not isinstance(columns, dict):
            raise BundleLineageError(f"invalid view block: {view_name!r}")
        parsed: dict[str, LineageEntry] = {}
        for column_name, entry_raw in columns.items():
            if not isinstance(column_name, str):
                raise BundleLineageError(f"invalid column key under {view_name!r}")
            parsed[column_name] = _parse_entry(entry_raw, view=view_name, column=column_name)
        lineage[view_name] = parsed
    return lineage


def lineage_entry(view: str, column: str) -> LineageEntry | None:
    return load_lineage().get(view, {}).get(column)


def register_policy_for_view_column(view: str, column: str) -> FieldPolicy | None:
    entry = lineage_entry(view, column)
    if entry is None or entry.derived:
        return None
    assert entry.table is not None and entry.column is not None
    return REGISTER.get((entry.table, entry.column))


def _member_sql_expression(bundle: DefinitionBundle, member: str) -> tuple[str, str] | None:
    for dimension in bundle.dimensions:
        if dimension.name == member:
            return dimension.owning_resource, dimension.sql_expression
    for measure in bundle.measures:
        if measure.name == member:
            return measure.owning_resource, measure.sql_expression
    for bucket in bundle.bucket_sets:
        if bucket.name == member or bucket.dimension == member:
            dim_name = bucket.dimension
            for dimension in bundle.dimensions:
                if dimension.name == dim_name:
                    return dimension.owning_resource, dimension.sql_expression
    return None


def _resource_view(bundle: DefinitionBundle, resource_name: str) -> str | None:
    for resource in bundle.resources:
        if resource.name == resource_name:
            return resource.projection_view
    return None


def _expression_identifiers(sql_expression: str) -> list[str]:
    """Every bare identifier token in a (possibly compound) SQL expression,
    in first-seen order. A bare column ("customer_name") yields exactly
    itself; a compound expression ("COALESCE(customer_name, status)" or
    "SUM(credit - debit)") yields every identifier it references."""
    without_strings = _STRING_LITERAL_RE.sub(" ", sql_expression)
    seen: set[str] = set()
    identifiers: list[str] = []
    for token in _IDENTIFIER_RE.findall(without_strings):
        if token not in seen:
            seen.add(token)
            identifiers.append(token)
    return identifiers


def _classified_references(view: str, sql_expression: str) -> list[tuple[str, FieldPolicy]]:
    """Every ``(identifier, policy)`` pair for register-classified columns
    referenced anywhere in ``sql_expression`` against ``view`` -- covers
    compound expressions, not just the whole-string bare-column case. An
    identifier that is a function name, SQL keyword, or another view's
    column simply has no lineage entry and is skipped."""
    references: list[tuple[str, FieldPolicy]] = []
    for identifier in _expression_identifiers(sql_expression):
        policy = register_policy_for_view_column(view, identifier)
        if policy is not None:
            references.append((identifier, policy))
    return references


def treatments_for_bundle_member(bundle: DefinitionBundle, member: str) -> list[FieldPolicy]:
    """Every register-classified policy among the columns a bundle member's
    SQL expression references -- may be more than one for a compound
    expression."""
    ref = _member_sql_expression(bundle, member)
    if ref is None:
        return []
    resource_name, sql_expression = ref
    view = _resource_view(bundle, resource_name)
    if view is None:
        return []
    return [policy for _identifier, policy in _classified_references(view, sql_expression)]


def forbidden_treatment_members(bundle: DefinitionBundle) -> list[str]:
    violations: list[str] = []
    seen: set[str] = set()
    for dimension in bundle.dimensions:
        for member in (dimension.name,):
            if member in seen:
                continue
            seen.add(member)
            if any(
                policy.treatment in _FORBIDDEN_TREATMENTS
                for policy in treatments_for_bundle_member(bundle, member)
            ):
                violations.append(member)
    for measure in bundle.measures:
        for member in (measure.name, measure.time_dimension):
            if member is None or member in seen:
                continue
            seen.add(member)
            if any(
                policy.treatment in _FORBIDDEN_TREATMENTS
                for policy in treatments_for_bundle_member(bundle, member)
            ):
                violations.append(member)
    for bucket in bundle.bucket_sets:
        for member in (bucket.name, bucket.dimension):
            if member in seen:
                continue
            seen.add(member)
            if any(
                policy.treatment in _FORBIDDEN_TREATMENTS
                for policy in treatments_for_bundle_member(bundle, member)
            ):
                violations.append(member)
    return violations


def classified_columns_for_plan(
    plan: BusinessQueryPlan,
    bundle: DefinitionBundle,
) -> list[ClassifiedColumn]:
    from app.business_query.plan import plan_member_names

    classified: list[ClassifiedColumn] = []
    seen: set[tuple[str, str, str]] = set()
    for member in sorted(plan_member_names(plan)):
        ref = _member_sql_expression(bundle, member)
        if ref is None:
            continue
        resource_name, sql_expression = ref
        view = _resource_view(bundle, resource_name)
        if view is None:
            continue
        for identifier, policy in _classified_references(view, sql_expression):
            key = (member, policy.table, policy.column)
            if key in seen:
                continue
            seen.add(key)
            classified.append(
                ClassifiedColumn(
                    member=member,
                    view=view,
                    view_column=identifier,
                    table=policy.table,
                    column=policy.column,
                    treatment=policy.treatment,
                    policy=policy,
                )
            )
    return classified


def assert_no_lineage_drift() -> None:
    """Every VIEW_COLUMNS column has lineage; no orphan lineage keys."""
    lineage = load_lineage()
    missing: list[str] = []
    for view, columns in VIEW_COLUMNS.items():
        view_lineage = lineage.get(view)
        if view_lineage is None:
            missing.extend(f"{view}.{col}" for col in sorted(columns))
            continue
        for column in sorted(columns):
            if column not in view_lineage:
                missing.append(f"{view}.{column}")
    orphans: list[str] = []
    for view, columns in lineage.items():
        expected = VIEW_COLUMNS.get(view)
        if expected is None:
            orphans.extend(f"{view}.{col}" for col in sorted(columns))
            continue
        for column in sorted(columns):
            if column not in expected:
                orphans.append(f"{view}.{column}")
    problems: list[str] = []
    if missing:
        problems.append("missing lineage: " + ", ".join(missing))
    if orphans:
        problems.append("orphan lineage: " + ", ".join(orphans))
    if problems:
        raise BundleLineageError("; ".join(problems))


def derived_conflicts_with_plain_mapping() -> list[str]:
    """Column names marked ``derived: true`` in one view while the SAME
    column name is plainly mapped (``{table, column}``) in another view,
    with no ``justification`` recorded. Almost always an author's mistake --
    conflating a derived JOIN key with a derived column -- and it silently
    disables the audit and the tier scan for that column, permanently and
    invisibly. ``assert_no_lineage_drift`` only checks key presence, so it
    cannot see a wrong ``derived`` value; this does."""
    lineage = load_lineage()
    plain_views_by_column: dict[str, list[str]] = {}
    for view, columns in lineage.items():
        for column, entry in columns.items():
            if not entry.derived:
                plain_views_by_column.setdefault(column, []).append(view)

    conflicts: list[str] = []
    for view, columns in sorted(lineage.items()):
        for column, entry in sorted(columns.items()):
            if not entry.derived or entry.justification is not None:
                continue
            plain_views = plain_views_by_column.get(column)
            if plain_views:
                where = ", ".join(sorted(plain_views))
                conflicts.append(f"{view}.{column} is derived but plainly mapped in: {where}")
    return conflicts


def assert_no_unjustified_derived_conflicts() -> None:
    conflicts = derived_conflicts_with_plain_mapping()
    if conflicts:
        raise BundleLineageError("; ".join(conflicts))
