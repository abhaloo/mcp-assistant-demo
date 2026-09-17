"""Field-level PII treatment register — the single source of truth for which
database field gets which treatment. Loaded fail-closed at import (ADR 0022)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Treatment(StrEnum):
    PSEUDONYMIZE = "pseudonymize"  # reversible token; restored for the user
    REDACT = "redact"  # one-way [REDACTED]; never restored
    SUPPRESS = "suppress"  # one-way [SUPPRESSED] + query-layer block; never restored


KNOWN_ENTITY_TYPES = {"PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION", "PII"}
_SEVERITY = {Treatment.PSEUDONYMIZE: 1, Treatment.REDACT: 2, Treatment.SUPPRESS: 3}
_DEFAULT_PATH = Path(__file__).parent / "pii_register.json"


@dataclass(frozen=True)
class FieldPolicy:
    table: str
    column: str
    treatment: Treatment
    entity_type: str
    category: str = ""
    sensitivity: str = ""
    subject: str = ""


class PiiRegisterError(RuntimeError):
    """Raised when the register cannot be loaded or validated. Fail-closed."""


def _required_string(item: dict, key: str, row: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PiiRegisterError(f"PII register row {row}: {key} must be a non-empty string")
    return value.strip()


def _optional_string(item: dict, key: str, row: int) -> str:
    value = item.get(key, "")
    if not isinstance(value, str):
        raise PiiRegisterError(f"PII register row {row}: {key} must be a string")
    return value


def load_register(path: Path) -> dict[tuple[str, str], FieldPolicy]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise PiiRegisterError(f"PII register file not found: {path}") from e
    except json.JSONDecodeError as e:
        raise PiiRegisterError(f"PII register is not valid JSON: {e}") from e

    if not isinstance(raw, list) or not raw:
        raise PiiRegisterError("PII register must be a non-empty JSON array")

    register: dict[tuple[str, str], FieldPolicy] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise PiiRegisterError(f"PII register row {i} must be an object")
        try:
            table = _required_string(item, "table", i).lower()
            column = _required_string(item, "column", i).lower()
            treatment = Treatment(_required_string(item, "treatment", i))
            entity_type = _required_string(item, "entity_type", i)
        except (KeyError, ValueError) as e:
            raise PiiRegisterError(f"PII register row {i} is invalid: {e}") from e

        if entity_type not in KNOWN_ENTITY_TYPES:
            raise PiiRegisterError(f"PII register row {i}: unknown entity_type {entity_type!r}")
        key = (table, column)
        if key in register:
            raise PiiRegisterError(f"PII register row {i}: duplicate policy for {key}")

        register[key] = FieldPolicy(
            table=table,
            column=column,
            treatment=treatment,
            entity_type=entity_type,
            category=_optional_string(item, "category", i),
            sensitivity=_optional_string(item, "sensitivity", i),
            subject=_optional_string(item, "subject", i),
        )
    return register


REGISTER: dict[tuple[str, str], FieldPolicy] = load_register(_DEFAULT_PATH)
REGISTER_TABLES: set[str] = {table for (table, _col) in REGISTER}


def resolve_policy(column: str, tables: set[str]) -> FieldPolicy | None:
    """A JOIN strips table qualification; a bare column may match several
    tables. Strictest treatment wins (suppress > redact > pseudonymize)."""
    col = column.lower()
    candidates = [REGISTER[(t, col)] for t in tables if (t, col) in REGISTER]
    if not candidates:
        return None
    return max(candidates, key=lambda p: _SEVERITY[p.treatment])


def suppress_columns(tables: set[str]) -> set[str]:
    """Column names with SUPPRESS treatment in any of `tables` (for the guard + prompt)."""
    keys = {t.lower() for t in tables}
    return {
        col for (t, col), p in REGISTER.items() if t in keys and p.treatment is Treatment.SUPPRESS
    }
