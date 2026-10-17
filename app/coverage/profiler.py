"""Per-column population facts and data profiles from the billing database."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.coverage.model import ColumnProfile, DataProfile, GeneratedFrom
from app.coverage.schema_inventory import SchemaColumn, SchemaInventory

_TEXT = {"varchar", "char", "text", "longtext", "mediumtext", "enum"}
_DATE = {"date", "datetime", "timestamp"}
_NUM = {"int", "bigint", "decimal", "double", "float", "tinyint"}
_SUPPORTED = _TEXT | _DATE | _NUM | {"json"}
_NUMERIC_PURE = "REGEXP '^[0-9]+(\\\\.[0-9]+)?$'"
HISTOGRAM_MAX_DISTINCT = 25
_FREE_TEXT = {
    "text",
    "tinytext",
    "mediumtext",
    "longtext",
    "json",
    "blob",
    "mediumblob",
    "longblob",
}
_SENSITIVE_TABLES = frozenset(
    {
        "users",
        "customers",
        "suppliers",
        "entities",
        "password_resets",
        "personal_access_tokens",
        "push_subscriptions",
        "sessions",
        "failed_jobs",
        "activity_log",
        "logs",
    }
)
_SENSITIVE_COLUMN_RE = re.compile(
    r"(^|_)(email|phone|mobile|tel|fax|address|tin|vrn|zrb|nid|passport|account_number"
    r"|account_no|swift|iban|bank|password|token|secret|api_key|username|first_name"
    r"|last_name|full_name|assigned_by|city|ip|user_agent)($|_)",
    re.IGNORECASE,
)


def _ident(name: str) -> str:
    """Quote a MySQL identifier, doubling any backtick inside it."""
    return "`" + name.replace("`", "``") + "`"


def histogram_allowed(column: SchemaColumn) -> bool:
    """Value histograms are for enum-like columns; free text and personal data never get one."""
    kind = column.data_type.lower()
    if kind in _FREE_TEXT or kind not in (_TEXT | _NUM):
        return False
    if column.table in _SENSITIVE_TABLES:
        return False
    return _SENSITIVE_COLUMN_RE.search(column.column) is None


def fk_target_for(column: SchemaColumn, inventory: SchemaInventory) -> str | None:
    """Find foreign key target table for an _id column."""
    if not column.column.endswith("_id"):
        return None
    stem = column.column.removesuffix("_id")
    candidates = (
        f"{stem}s",
        f"{stem}es",
        f"{stem[:-1]}ies" if stem.endswith("y") else None,
    )
    for candidate in candidates:
        if candidate and candidate in inventory.tables:
            return candidate
    return None


def profile_sql(column: SchemaColumn, fk_target: str | None) -> str:
    """Construct SQL query for per-column population metrics."""
    c, t = _ident(column.column), _ident(column.table)
    kind = column.data_type.lower()
    numeric_pure = f"SUM(`s`.{c} {_NUMERIC_PURE})" if kind in _TEXT else "NULL"
    max_length = f"MAX(CHAR_LENGTH({c}))" if kind in _TEXT else "NULL"
    min_year = f"MIN(YEAR({c}))" if kind in _DATE else "NULL"
    max_year = f"MAX(YEAR({c}))" if kind in _DATE else "NULL"
    orphan = f"SUM(`s`.{c} IS NOT NULL AND `t`.`id` IS NULL)" if fk_target else "NULL"
    join = f" LEFT JOIN {_ident(fk_target)} `t` ON `t`.`id` = `s`.{c}" if fk_target else ""
    return (
        f"SELECT COUNT(*) AS row_count, "
        f"SUM(`s`.{c} IS NULL) AS null_count, "
        f"COUNT(DISTINCT `s`.{c}) AS distinct_count, "
        f"{numeric_pure} AS numeric_pure_count, "
        f"{max_length} AS max_length, "
        f"{min_year} AS min_year, "
        f"{max_year} AS max_year, "
        f"{orphan} AS orphan_count "
        f"FROM {t} `s`{join}"
    )


def histogram_sql(column: SchemaColumn) -> str:
    """Construct SQL query for top values histogram."""
    c, t = _ident(column.column), _ident(column.table)
    return (
        f"SELECT {c} AS v, COUNT(*) AS n FROM {t} "
        f"GROUP BY {c} ORDER BY n DESC LIMIT {HISTOGRAM_MAX_DISTINCT}"
    )


def _profile_column_with_conn(
    conn: Connection,
    table: str,
    column: SchemaColumn,
    fk_target: str | None = None,
) -> ColumnProfile:
    """Compute profile for a single column using an active connection."""
    kind = column.data_type.lower()
    if kind not in _SUPPORTED:
        count_sql = text(
            f"SELECT COUNT(*), SUM({_ident(column.column)} IS NULL) FROM {_ident(table)}"
        )
        count = conn.execute(count_sql).one()
        return ColumnProfile(
            table=table,
            column=column.column,
            data_type=kind,
            row_count=int(count[0]),
            null_count=int(count[1] or 0),
            profile_note="type_unsupported",
        )

    r = conn.execute(text(profile_sql(column, fk_target))).mappings().one()
    row_count = int(r["row_count"])
    null_count = int(r["null_count"] or 0)
    distinct_count = None if r["distinct_count"] is None else int(r["distinct_count"])
    numeric_pure_count = None if r["numeric_pure_count"] is None else int(r["numeric_pure_count"])
    max_length = None if r["max_length"] is None else int(r["max_length"])
    min_year = None if r["min_year"] is None else int(r["min_year"])
    max_year = None if r["max_year"] is None else int(r["max_year"])
    orphan_count = None if r["orphan_count"] is None else int(r["orphan_count"])

    histogram = None
    suppressed = False
    if distinct_count is not None and distinct_count <= HISTOGRAM_MAX_DISTINCT:
        if histogram_allowed(column):
            histogram = {
                str(h["v"]): int(h["n"])
                for h in conn.execute(text(histogram_sql(column))).mappings()
            }
        elif kind in (_TEXT | _NUM):
            suppressed = True

    profile_note = None
    if row_count > 0 and null_count == row_count:
        profile_note = "always null"
    elif (
        kind in _TEXT
        and numeric_pure_count is not None
        and numeric_pure_count > 0
        and numeric_pure_count == (row_count - null_count)
    ):
        profile_note = "numeric string in text col"

    return ColumnProfile(
        table=table,
        column=column.column,
        data_type=kind,
        row_count=row_count,
        null_count=null_count,
        distinct_count=distinct_count,
        numeric_pure_count=numeric_pure_count,
        max_length=max_length,
        min_year=min_year,
        max_year=max_year,
        value_histogram=histogram,
        histogram_suppressed=suppressed,
        orphan_count=orphan_count,
        profile_note=profile_note,
    )


def profile_column(
    engine_or_conn: Engine | Connection,
    column: SchemaColumn,
    *,
    fk_target: str | None = None,
) -> ColumnProfile:
    """Profile a single database column."""
    if isinstance(engine_or_conn, Connection):
        return _profile_column_with_conn(engine_or_conn, column.table, column, fk_target=fk_target)
    with engine_or_conn.connect() as conn:
        return _profile_column_with_conn(conn, column.table, column, fk_target=fk_target)


def profile_columns(
    engine: Engine,
    inventory: SchemaInventory,
    generated_from: GeneratedFrom | None = None,
    tables: list[str] | None = None,
) -> DataProfile:
    """Profile columns across requested tables or the full schema inventory.

    One failing column yields one row carrying a profile_error note; the run continues.
    """
    gen = generated_from
    if gen is None:
        gen = GeneratedFrom(
            db_schema_hash=inventory.schema_hash,
            manifest_hash="unknown",
            bundle_hash="unknown",
            rag_commit="unknown",
            generated_utc=datetime.now(UTC).isoformat(),
        )

    wanted = tables or sorted(inventory.tables.keys())
    columns: list[ColumnProfile] = []

    with engine.connect() as conn:
        for table_name in wanted:
            if table_name not in inventory.tables:
                continue
            for col_desc in inventory.tables[table_name]:
                fk_target = fk_target_for(col_desc, inventory)
                try:
                    col_profile = _profile_column_with_conn(
                        conn, table_name, col_desc, fk_target=fk_target
                    )
                except SQLAlchemyError as err:
                    col_profile = ColumnProfile(
                        table=table_name,
                        column=col_desc.column,
                        data_type=col_desc.data_type.lower(),
                        row_count=0,
                        null_count=0,
                        profile_note=f"profile_error:{type(err).__name__}",
                    )
                columns.append(col_profile)

    return DataProfile(generated_from=gen, columns=columns)
