"""Build the disposable eval database for the Business Query suite.

The module reads the ai_v1_bq_* semantic views, which a raw billing dump does not
carry. Rather than copying 37MB, this creates a thin database of PASSTHROUGH views
onto the frozen snapshot (`CREATE VIEW bills AS SELECT * FROM <snapshot>.bills`),
so the semantic views can be layered on top while the snapshot itself is only ever
read. Nothing is copied, so nothing can drift from the snapshot.

Protected databases are refused outright, and the target name must match the
disposable pattern — a typo cannot reach production data.
"""

from __future__ import annotations

import argparse
import re
import sys

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

# Never create, drop, or write to any of these under any circumstances.
PROTECTED = frozenset({"mcp_local", "mcp_eval", "multicolor-db", "mysql", "information_schema"})

# The disposable target must look like one. Belt and braces alongside PROTECTED.
DISPOSABLE_PATTERN = re.compile(r"^mcp_bq_eval(_[a-z0-9]+)?$")


def _guard(name: str) -> str:
    if name in PROTECTED:
        raise SystemExit(f"refused: {name} is protected")
    if not DISPOSABLE_PATTERN.match(name):
        raise SystemExit(f"refused: {name} is not a disposable eval database name")
    return name


def _admin_engine(env_key: str = "MCP_BILLING_DATABASE_URL"):
    url = dotenv_values(".env").get(env_key)
    if not url:
        raise SystemExit(f"{env_key} is not set in .env")
    return create_engine(make_url(url).set(database="information_schema"))


def create(target: str, snapshot: str) -> int:
    _guard(target)
    if snapshot not in PROTECTED:
        print(f"note: snapshot {snapshot} is not in the protected set — reading it anyway")
    engine = _admin_engine()
    with engine.connect() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_type = 'BASE TABLE' ORDER BY table_name"
                ),
                {"s": snapshot},
            ).fetchall()
        ]
        if not tables:
            raise SystemExit(f"snapshot {snapshot} has no base tables — wrong database?")

        conn.execute(text(f"DROP DATABASE IF EXISTS `{target}`"))
        conn.execute(text(f"CREATE DATABASE `{target}`"))
        for table in tables:
            conn.execute(
                text(f"CREATE VIEW `{target}`.`{table}` AS SELECT * FROM `{snapshot}`.`{table}`")
            )
        conn.commit()
    print(f"created {target}: {len(tables)} passthrough views onto {snapshot}")
    return 0


def verify(target: str) -> int:
    _guard(target)
    engine = _admin_engine()
    with engine.connect() as conn:
        views = conn.execute(
            text("SELECT COUNT(*) FROM information_schema.views WHERE table_schema = :t"),
            {"t": target},
        ).scalar()
        semantic = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT table_name FROM information_schema.views "
                    "WHERE table_schema = :t AND table_name LIKE 'ai_v1_bq_%' ORDER BY table_name"
                ),
                {"t": target},
            ).fetchall()
        ]
        bills = conn.execute(text(f"SELECT COUNT(*) FROM `{target}`.bills")).scalar()
    print(f"{target}: {views} views total, {len(semantic)} semantic")
    for name in semantic:
        print("   ", name)
    print(f"bills readable through passthrough: {bills}")
    return 0 if semantic else 1


def drop(target: str) -> int:
    _guard(target)
    engine = _admin_engine()
    with engine.connect() as conn:
        present = conn.execute(
            text("SELECT SCHEMA_NAME FROM information_schema.schemata WHERE SCHEMA_NAME = :t"),
            {"t": target},
        ).scalar()
        if not present:
            print(f"{target} not present — nothing to drop")
            return 0
        conn.execute(text(f"DROP DATABASE `{target}`"))
        conn.commit()
        remaining = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT SCHEMA_NAME FROM information_schema.schemata "
                    "WHERE SCHEMA_NAME IN ('mcp_local','mcp_eval','multicolor-db')"
                )
            ).fetchall()
        ]
    print(f"dropped {target}; protected databases still present: {sorted(remaining)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["create", "verify", "drop"])
    ap.add_argument("--target", default="mcp_bq_eval")
    ap.add_argument("--snapshot", default="mcp_eval")
    args = ap.parse_args()
    if args.action == "create":
        return create(args.target, args.snapshot)
    if args.action == "verify":
        return verify(args.target)
    return drop(args.target)


if __name__ == "__main__":
    sys.exit(main())
