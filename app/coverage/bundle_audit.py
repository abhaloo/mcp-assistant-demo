"""Bundle allowed_values audited against the live values of each projection view."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.business_query.definitions.allowed_values import normalise
from app.business_query.definitions.schema import DimensionDefinition, ResourceBinding
from app.business_query.definitions.view_columns import _SQL_IDENTIFIER_RE
from app.coverage.model import AuditFinding, BundleAudit, GeneratedFrom
from app.coverage.profiler import _SENSITIVE_COLUMN_RE, _ident, histogram_allowed
from app.coverage.render_markdown import generated_from_block, md_table
from app.coverage.schema_inventory import SchemaColumn

MAX_ENUM_VALUES = 25
ValueSet = list[str | None]
ValueSetReader = Callable[[ResourceBinding, str], ValueSet | None]


def value_set_query(
    resource: ResourceBinding, sql_expression: str
) -> tuple[str, dict[str, object]]:
    """Group the dimension's expression over its view under the resource's record predicates."""
    expr = sql_expression.strip()
    if not _SQL_IDENTIFIER_RE.fullmatch(expr):
        raise ValueError(f"sql_expression is not a single identifier: {sql_expression!r}")
    params: dict[str, object] = {f"p_{k}": v for k, v in resource.record_predicates.items()}
    where = " AND ".join(f"{_ident(k)} = :p_{k}" for k in resource.record_predicates)
    where_sql = f" WHERE {where}" if where else ""
    sql = (
        f"SELECT {_ident(expr)} AS v, COUNT(*) AS n FROM {_ident(resource.projection_view)}"
        f"{where_sql} GROUP BY v ORDER BY n DESC LIMIT {MAX_ENUM_VALUES + 1}"
    )
    return sql, params


def live_value_sets(engine: Engine) -> ValueSetReader:
    """Read distinct values from the database; None past the cap; SQL NULL stays None."""

    def read(resource: ResourceBinding, sql_expression: str) -> ValueSet | None:
        sql, params = value_set_query(resource, sql_expression)
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).all()
        if len(rows) > MAX_ENUM_VALUES:
            return None
        return [None if row[0] is None else str(row[0]) for row in rows]

    return read


def is_sensitive(dimension: DimensionDefinition, base_column: SchemaColumn | None) -> bool:
    """Personal data and free text never get their values written into an artifact."""
    field = dimension.name.split(".", 1)[-1]
    if field in {"customer_name", "supplier_name"} or _SENSITIVE_COLUMN_RE.search(field):
        return True
    if base_column is not None:
        return not histogram_allowed(base_column)
    return False


def _php_list(values: list[str]) -> str:
    quoted = ", ".join("'" + v.replace("'", "\\'") + "'" for v in values)
    return f"allowedValues: [{quoted}],"


def _finding(dimension: DimensionDefinition, **fields: object) -> AuditFinding:
    return AuditFinding(member=f"dimension:{dimension.name}", **fields)


def _collisions(allowed: list[str]) -> list[str]:
    by_norm: dict[str, list[str]] = {}
    for value in allowed:
        by_norm.setdefault(normalise(value), []).append(value)
    return sorted(v for group in by_norm.values() if len(group) > 1 for v in group)


def audit_dimensions(
    dimensions: list[DimensionDefinition],
    resources: list[ResourceBinding],
    read_values: ValueSetReader,
    *,
    base_columns: dict[str, SchemaColumn | None],
    reference_values: dict[str, list[str]] | None,
) -> list[AuditFinding]:
    """One finding per rule per string dimension.

    The gate compares the view's live values with the list under the same `normalise` as
    compile, and refuses lists whose entries collide after normalisation.
    """
    by_resource = {r.name: r for r in resources}
    findings: list[AuditFinding] = []
    for dimension in dimensions:
        if dimension.type != "string" or dimension.is_primary_key:
            continue
        if is_sensitive(dimension, base_columns.get(dimension.name)):
            findings.append(
                _finding(dimension, rule="not_auditable", severity="advice", reason="sensitive")
            )
            continue
        expr = dimension.sql_expression.strip()
        if not _SQL_IDENTIFIER_RE.fullmatch(expr):
            findings.append(
                _finding(
                    dimension,
                    rule="not_auditable",
                    severity="advice",
                    reason="not_a_single_identifier",
                )
            )
            continue
        live = read_values(by_resource[dimension.owning_resource], expr)
        if live is None:
            findings.append(
                _finding(
                    dimension,
                    rule="not_auditable",
                    severity="advice",
                    reason="more_than_25_distinct",
                )
            )
            continue
        if live and all(v is None for v in live):
            findings.append(_finding(dimension, rule="always_null", severity="advice"))
            continue
        live_values = sorted({v for v in live if v is not None})
        allowed = list(dimension.allowed_values)
        if allowed:
            colliding = _collisions(allowed)
            if colliding:
                findings.append(
                    _finding(
                        dimension, rule="allowed_values_collide", severity="gate", values=colliding
                    )
                )
            allowed_norm = {normalise(a) for a in allowed}
            live_norm = {normalise(v) for v in live_values}
            missing = sorted(v for v in live_values if normalise(v) not in allowed_norm)
            absent = sorted(a for a in allowed if normalise(a) not in live_norm)
            if missing:
                findings.append(
                    _finding(
                        dimension,
                        rule="value_missing_from_allowed",
                        severity="gate",
                        values=missing,
                    )
                )
            if absent:
                findings.append(
                    _finding(
                        dimension,
                        rule="allowed_value_absent_from_data",
                        severity="advice",
                        values=absent,
                    )
                )
            continue
        if reference_values is None:
            findings.append(
                _finding(
                    dimension, rule="not_auditable", severity="advice", reason="profile_missing"
                )
            )
            continue
        proposed = sorted(set(live_values) | set(reference_values.get(dimension.name, [])))
        findings.append(
            _finding(
                dimension,
                rule="enum_like_without_allowed_values",
                severity="advice",
                values=proposed,
                proposal=_php_list(proposed),
            )
        )
    return findings


def build_audit(findings: list[AuditFinding], generated_from: GeneratedFrom) -> BundleAudit:
    """Gate findings first, then by member and rule, so a reader sees what blocks a publish."""
    ordered = sorted(findings, key=lambda f: (f.severity != "gate", f.member, f.rule))
    return BundleAudit(
        generated_from=generated_from,
        findings=ordered,
        gate_count=sum(1 for f in ordered if f.severity == "gate"),
    )


def render_audit(audit: BundleAudit) -> str:
    rows = [
        [
            f.severity,
            f"`{f.member}`",
            f.rule,
            ", ".join(f.values),
            f"`{f.proposal}`" if f.proposal else (f.reason or ""),
        ]
        for f in audit.findings
    ]
    body = md_table(["severity", "member", "rule", "values", "proposal / reason"], rows)
    return (
        "# Bundle audit\n\n"
        f"{generated_from_block(audit.generated_from)}\n\n"
        f"Gate findings: {audit.gate_count}. A gate finding blocks a publish.\n\n"
        f"{body}\n"
    )
