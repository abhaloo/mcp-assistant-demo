"""Read-only MySQL billing engine for the eval SQL agent.

Lazy singleton: built on first use via get_engine(), not at import.
Owned and disposed by the eval harness and CLI runners on completion.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

from app.config import settings

_ENGINE: Engine | None = None


def _set_session_read_only(conn: Connection) -> None:
    conn.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")


def get_engine() -> Engine:
    """Build (once) and return the shared read-only billing engine."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(
            settings.mcp_billing_database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=280,
            connect_args={"connect_timeout": 10},
        )

        if settings.billing_db_read_only:
            event.listen(_ENGINE, "begin", _set_session_read_only)

    return _ENGINE


def dispose_engine() -> None:
    """Close pooled connections and clear the cached singleton."""
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
        _ENGINE = None


def reset_engine_for_tests() -> None:
    """Drop the cached engine so tests can point at a different DATABASE_URL."""
    global _ENGINE
    _ENGINE = None
