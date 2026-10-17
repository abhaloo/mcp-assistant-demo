"""Query-relevant database content manifest for arm-boundary drift detection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TableSnapshot:
    name: str
    schema: tuple[str, ...]
    row_count: int
    row_digest: str
    permissions: tuple[str, ...]
    indexes: tuple[str, ...]


def digest_rows(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    canonical = []
    for row in sorted(rows, key=lambda item: json.dumps(item, sort_keys=True, default=str)):
        canonical.append([row.get(column) for column in columns])
    payload = json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def table_snapshot(
    name: str,
    *,
    schema: tuple[str, ...],
    rows: list[dict[str, Any]],
    permissions: tuple[str, ...] = (),
    indexes: tuple[str, ...] = (),
) -> TableSnapshot:
    return TableSnapshot(
        name=name,
        schema=schema,
        row_count=len(rows),
        row_digest=digest_rows(rows, schema),
        permissions=tuple(permissions),
        indexes=tuple(indexes),
    )


def content_manifest(tables: tuple[TableSnapshot, ...]) -> dict[str, Any]:
    body = {
        table.name: {
            "schema": list(table.schema),
            "row_count": table.row_count,
            "row_digest": table.row_digest,
            "permissions": list(table.permissions),
            "indexes": list(table.indexes),
        }
        for table in sorted(tables, key=lambda item: item.name)
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return {
        "tables": body,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


class DatabaseContentDrift(ValueError):
    """Query-relevant database content changed between arms."""


def assert_manifests_equal(before: dict[str, Any], after: dict[str, Any]) -> None:
    if before.get("sha256") != after.get("sha256"):
        raise DatabaseContentDrift("query-relevant database content drifted between arms")
