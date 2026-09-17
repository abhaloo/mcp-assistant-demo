"""SQL-agent execution records and PII types for provenance and telemetry."""

from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError


class SuppressedColumnError(SQLAlchemyError):
    """Generated SQL referenced a suppress-treatment column.

    Subclasses SQLAlchemyError so SQLDatabase.run_no_throw catches it and
    returns it to the agent as a recoverable error instead of crashing the run.
    """


@dataclass
class AnonymizerStats:
    tokens_created: int = 0
    redacted_values: int = 0
    suppressed_values: int = 0
    anonymize_calls: int = 0
    entities_detected: int = 0


@dataclass(frozen=True)
class SqlExecutionRecord:
    """Typed row identity captured at the DB wrapper — never inferred from answer text."""

    table: str
    record_id: int
    label: str
