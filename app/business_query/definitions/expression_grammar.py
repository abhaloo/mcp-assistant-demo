"""Validation of closed-grammar SQL expressions in business-definition bundles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.business_query.definitions.schema import detail_source_columns
from app.business_query.definitions.view_columns import (
    _ALLOWED_FUNCTIONS,
    _BANNED_SUBSTRINGS,
    _SQL_IDENTIFIER_RE,
    _SQL_KEYWORDS,
    _TOKEN_RE,
    DETAIL_VIEW_COLUMNS,
    VIEW_COLUMNS,
)

if TYPE_CHECKING:
    from app.business_query.definitions.schema import DefinitionBundle


def _resource_columns(bundle: DefinitionBundle) -> dict[str, frozenset[str]]:
    by_name: dict[str, frozenset[str]] = {}
    for resource in bundle.resources:
        cols = VIEW_COLUMNS.get(resource.projection_view) or DETAIL_VIEW_COLUMNS.get(
            resource.projection_view
        )
        if cols is None:
            by_name[resource.name] = frozenset()
        else:
            by_name[resource.name] = cols
    return by_name


def _validate_expression(expression: str, *, allowed: frozenset[str], context: str) -> list[str]:
    violations: list[str] = []
    upper = expression.upper()
    for banned in _BANNED_SUBSTRINGS:
        found = banned.upper() in upper if banned[0].isalpha() else banned in expression
        if found:
            violations.append(f"{context}: banned token {banned!r} in {expression!r}")
    if violations:
        return violations

    for match in _TOKEN_RE.finditer(expression):
        kind = match.lastgroup
        text = match.group()
        if kind in {"ws", "op", "punct", "num"}:
            continue
        if kind == "str":
            inner = text[1:-1]
            if "'" in inner or "\\" in inner:
                violations.append(
                    f"{context}: string literal must not embed quote/backslash: {text!r}"
                )
            continue
        if kind == "ident":
            name = text.upper()
            if name in _ALLOWED_FUNCTIONS or name in _SQL_KEYWORDS:
                continue
            if not _SQL_IDENTIFIER_RE.fullmatch(text):
                violations.append(f"{context}: invalid identifier {text!r}")
            elif text not in allowed:
                violations.append(f"{context}: identifier {text!r} is not a declared view column")
            continue
        violations.append(f"{context}: unexpected token {text!r} in {expression!r}")
    return violations


def _collect_expression_violations(bundle: DefinitionBundle) -> list[str]:
    columns = _resource_columns(bundle)
    violations: list[str] = []

    detail_sources = {source.family_key: source for source in bundle.detail_sources}
    detail_revisions: set[tuple[str, str]] = set()
    for detail in bundle.detail_definitions:
        revision_key = (detail.family_key, detail.revision_hash)
        if revision_key in detail_revisions:
            violations.append(
                f"detail definition {detail.family_key!r}: duplicate revision_hash "
                f"{detail.revision_hash!r}"
            )
        detail_revisions.add(revision_key)
        source = detail_sources.get(detail.family_key)
        if source is None:
            violations.append(f"detail definition {detail.family_key!r}: no signed detail source")
            continue
        detail_columns = detail_source_columns(source)
        if not detail_columns:
            violations.append(
                f"detail definition {detail.family_key!r}: signed source has no columns"
            )
        if source.projection_view != detail.physical_source:
            violations.append(
                f"detail definition {detail.family_key!r}: physical_source does not match "
                "signed source projection_view"
            )
        if detail.owner_column not in detail_columns:
            violations.append(
                f"detail definition {detail.family_key!r}: owner_column "
                f"{detail.owner_column!r} not on physical source"
            )
        if detail.value_column not in detail_columns:
            violations.append(
                f"detail definition {detail.family_key!r}: value_column "
                f"{detail.value_column!r} not on physical source"
            )
        for label, col in (
            ("entity", detail.scope_columns.entity),
            ("department", detail.scope_columns.department),
        ):
            if col is not None and col not in detail_columns:
                violations.append(
                    f"detail definition {detail.family_key!r}: scope_columns.{label} "
                    f"{col!r} not on physical source"
                )
        if "union" in detail.physical_source.lower() or "union" in detail.logical_source.lower():
            violations.append(
                f"detail definition {detail.family_key!r}: UNION sources are not allowed"
            )

    for resource in bundle.resources:
        allowed = columns.get(resource.name, frozenset())
        if (
            resource.projection_view not in VIEW_COLUMNS
            and resource.projection_view not in DETAIL_VIEW_COLUMNS
        ):
            violations.append(
                f"resource {resource.name!r}: unknown projection_view {resource.projection_view!r}"
            )
        if resource.primary_key not in allowed:
            violations.append(
                f"resource {resource.name!r}: primary_key {resource.primary_key!r} not on view"
            )
        for col, lit in resource.record_predicates.items():
            if col not in allowed:
                violations.append(
                    f"resource {resource.name!r}: record_predicates key {col!r} not on view"
                )
            del lit
        for label, col in (
            ("entity", resource.scope_columns.entity),
            ("department", resource.scope_columns.department),
        ):
            if col is not None and col not in allowed:
                violations.append(
                    f"resource {resource.name!r}: scope_columns.{label} {col!r} not on view"
                )

    for measure in bundle.measures:
        if measure.agg_type == "derived":
            continue
        allowed = columns.get(measure.owning_resource, frozenset())
        if measure.owning_resource not in columns:
            violations.append(
                f"measure {measure.name!r}: owning_resource "
                f"{measure.owning_resource!r} not declared"
            )
        violations.extend(
            _validate_expression(
                measure.sql_expression,
                allowed=allowed,
                context=f"measure {measure.name!r} sql_expression",
            )
        )
        if measure.filter_sql:
            violations.extend(
                _validate_expression(
                    measure.filter_sql,
                    allowed=allowed,
                    context=f"measure {measure.name!r} filter_sql",
                )
            )

    for dimension in bundle.dimensions:
        allowed = columns.get(dimension.owning_resource, frozenset())
        if dimension.owning_resource not in columns:
            violations.append(
                f"dimension {dimension.name!r}: owning_resource "
                f"{dimension.owning_resource!r} not declared"
            )
        violations.extend(
            _validate_expression(
                dimension.sql_expression,
                allowed=allowed,
                context=f"dimension {dimension.name!r} sql_expression",
            )
        )

    for bucket in bundle.bucket_sets:
        allowed = columns.get(bucket.owning_resource, frozenset())
        if bucket.owning_resource not in columns:
            violations.append(
                f"bucket_set {bucket.name!r}: owning_resource "
                f"{bucket.owning_resource!r} not declared"
            )
        violations.extend(
            _validate_expression(
                bucket.applies_filter_sql,
                allowed=allowed,
                context=f"bucket_set {bucket.name!r} applies_filter_sql",
            )
        )
        if bucket.dimension:
            violations.extend(
                _validate_expression(
                    bucket.dimension,
                    allowed=allowed,
                    context=f"bucket_set {bucket.name!r} dimension",
                )
            )
        for bucket_label, predicate in bucket.bucket_predicates:
            violations.extend(
                _validate_expression(
                    predicate,
                    allowed=allowed,
                    context=f"bucket_set {bucket.name!r} bucket {bucket_label!r}",
                )
            )

    for segment in bundle.segments:
        allowed = columns.get(segment.owning_resource, frozenset())
        if segment.owning_resource not in columns:
            violations.append(
                f"segment {segment.name!r}: owning_resource "
                f"{segment.owning_resource!r} not declared"
            )
        violations.extend(
            _validate_expression(
                segment.predicate_sql,
                allowed=allowed,
                context=f"segment {segment.name!r} predicate_sql",
            )
        )

    for join in bundle.joins:
        left = columns.get(join.from_resource, frozenset())
        right = columns.get(join.to_resource, frozenset())
        if join.from_resource not in columns or join.to_resource not in columns:
            violations.append(
                f"join {join.from_resource!r}->{join.to_resource!r}: resource not declared"
            )
        violations.extend(
            _validate_expression(
                join.on_sql,
                allowed=left | right,
                context=f"join {join.from_resource!r}->{join.to_resource!r} on_sql",
            )
        )

    return violations
