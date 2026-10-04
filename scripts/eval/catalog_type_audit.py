"""Audit type: number catalog dimensions against database column types."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import text

from app.business_query.definitions.loader import current_bundle
from app.business_query.definitions.schema import DefinitionBundle
from app.eval.business_query.harness import billing_engine

_NUMERIC_SQL_TYPES = frozenset(
    {
        "int",
        "integer",
        "bigint",
        "smallint",
        "tinyint",
        "decimal",
        "numeric",
        "float",
        "double",
    }
)


@dataclass(frozen=True)
class AuditFinding:
    member: str
    column: str
    sql_type: str


def audit_numeric_declarations(
    bundle: DefinitionBundle,
    column_types: Mapping[tuple[str, str], str],
) -> list[AuditFinding]:
    """Report numeric dimensions whose backing database column is non-numeric."""
    resources_by_name = {r.name: r for r in bundle.resources}
    findings: list[AuditFinding] = []

    for dimension in bundle.dimensions:
        if dimension.type != "number":
            continue

        expr = dimension.sql_expression
        if " " in expr or "(" in expr or ")" in expr:
            continue

        resource = resources_by_name.get(dimension.owning_resource)
        if resource is None:
            continue

        table_name = resource.projection_view
        column_name = expr
        sql_type = column_types.get((table_name, column_name))

        if sql_type is None or sql_type.lower() not in _NUMERIC_SQL_TYPES:
            findings.append(
                AuditFinding(
                    member=dimension.name,
                    column=f"{table_name}.{column_name}",
                    sql_type=sql_type or "missing",
                )
            )

    return findings


def _count_checkable_dimensions(bundle: DefinitionBundle) -> int:
    resources_by_name = {r.name: r for r in bundle.resources}
    return sum(
        1
        for d in bundle.dimensions
        if d.type == "number"
        and not (" " in d.sql_expression or "(" in d.sql_expression or ")" in d.sql_expression)
        and d.owning_resource in resources_by_name
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit type: number dimensions against information_schema column types."
    )
    parser.add_argument("db", nargs="?", default=None, help="Target database name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the count of dimensions to check and exit without database connection",
    )
    args = parser.parse_args()

    bundle = current_bundle()

    if args.dry_run:
        count = _count_checkable_dimensions(bundle)
        print(f"Would check {count} numeric dimensions")
        sys.exit(0)

    if not args.db:
        parser.error("database name is required when not running with --dry-run")

    engine = billing_engine(args.db)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = DATABASE()"
            )
        ).fetchall()

    column_types = {(row[0], row[1]): row[2] for row in rows}
    findings = audit_numeric_declarations(bundle, column_types)

    for finding in findings:
        print(f"{finding.member}: {finding.column} ({finding.sql_type})")

    if findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
