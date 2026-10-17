"""Per-engine, per-table-set cache of reflected SQLAlchemy MetaData.

Reflection is a DB round-trip (over the cloudflared tunnel in prod) that the SQL
agent path otherwise pays on EVERY request. The reflected schema is request-
independent and immutable after population, so it is safe to share read-only
across concurrent requests. Keyed by engine IDENTITY (WeakKeyDictionary) so the
eval harness's runtime engine swap transparently gets its own reflected schema.
"""

from __future__ import annotations

import threading
import weakref

from sqlalchemy import MetaData
from sqlalchemy.engine import Engine
from sqlalchemy.types import NullType

_lock = threading.Lock()
_cache: weakref.WeakKeyDictionary[Engine, dict[frozenset[str], MetaData]] = (
    weakref.WeakKeyDictionary()
)


def get_reflected_metadata(engine: Engine, include_tables: list[str]) -> MetaData:
    key = frozenset(include_tables)
    with _lock:
        per_engine = _cache.get(engine)
        if per_engine is None:
            per_engine = {}
            _cache[engine] = per_engine
        md = per_engine.get(key)
        if md is None:
            md = MetaData()
            # Reflect the FULL include set now so request-time get_table_info never
            # lazily reflects (which would mutate this shared MetaData and race readers).
            md.reflect(bind=engine, only=sorted(include_tables))
            # Pre-strip NullType columns under the lock (mirrors langchain SQLDatabase).
            for table in md.tables.values():
                for col in [c for c in table.columns if type(c.type) is NullType]:
                    table._columns.remove(col)  # noqa: SLF001 — mirrors langchain's own op
            per_engine[key] = md
        return md
