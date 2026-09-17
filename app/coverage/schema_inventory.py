"""Tables, columns, and view definitions of the database schema, plus a stable hash."""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.coverage.model import _Strict

_COLUMNS_SQL = text(
    "SELECT table_name, column_name, data_type, is_nullable, column_key "
    "FROM information_schema.columns WHERE table_schema = DATABASE() "
    "ORDER BY table_name, ordinal_position"
)
_VIEWS_SQL = text(
    "SELECT table_name, view_definition FROM information_schema.views "
    "WHERE table_schema = DATABASE() ORDER BY table_name"
)


class SchemaColumn(_Strict):
    """Database column descriptor with data type, nullability, and primary key status."""

    table: str
    column: str
    data_type: str
    is_nullable: bool
    is_pk: bool = False


class SchemaView(_Strict):
    """Database view descriptor with view name and SQL definition."""

    name: str
    definition: str


class SchemaInventory(_Strict):
    """Inventory of base tables and views with a content-derived schema hash."""

    tables: dict[str, list[SchemaColumn]]
    views: dict[str, SchemaView]
    schema_hash: str = ""


def build_inventory(
    tables: dict[str, list[SchemaColumn]],
    views: dict[str, SchemaView],
) -> SchemaInventory:
    """Build a deterministic SchemaInventory from tables and views.

    Views are excluded from the tables mapping. Tables and columns are sorted deterministically.
    Computes a content-derived sha256 schema hash over all column tuples.
    """
    clean_tables: dict[str, list[SchemaColumn]] = {}
    for table_name in sorted(tables.keys()):
        if table_name in views:
            continue
        cols = sorted(tables[table_name], key=lambda c: c.column)
        clean_tables[table_name] = cols

    sorted_views: dict[str, SchemaView] = {name: views[name] for name in sorted(views.keys())}

    tuples: list[tuple[str, str, str, bool, bool]] = sorted(
        (col.table, col.column, col.data_type, col.is_nullable, col.is_pk)
        for cols in clean_tables.values()
        for col in cols
    )
    lines = [f"{t[0]}\t{t[1]}\t{t[2]}\t{t[3]}\t{t[4]}" for t in tuples]
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()

    return SchemaInventory(
        tables=clean_tables,
        views=sorted_views,
        schema_hash=f"sha256:{digest}",
    )


def inventory_schema(engine: Engine) -> SchemaInventory:
    """Introspect tables, columns, and views from the connected database."""
    with engine.connect() as conn:
        view_rows = conn.execute(_VIEWS_SQL).fetchall()
        views = {row[0]: SchemaView(name=row[0], definition=row[1] or "") for row in view_rows}

        column_rows = conn.execute(_COLUMNS_SQL).fetchall()
        raw_tables: dict[str, list[SchemaColumn]] = {}
        for row in column_rows:
            table_name = str(row[0])
            if table_name in views:
                continue
            col_name = str(row[1])
            data_type = str(row[2])
            is_nullable = str(row[3]).upper() == "YES"
            is_pk = str(row[4]).upper() == "PRI"
            col = SchemaColumn(
                table=table_name,
                column=col_name,
                data_type=data_type,
                is_nullable=is_nullable,
                is_pk=is_pk,
            )
            raw_tables.setdefault(table_name, []).append(col)

    return build_inventory(raw_tables, views)


def database_name(engine: Engine) -> str:
    """Name of the schema the engine is connected to."""
    with engine.connect() as conn:
        name = conn.execute(text("SELECT DATABASE()")).scalar()
    if not name:
        raise RuntimeError("connection has no default database")
    return str(name)
