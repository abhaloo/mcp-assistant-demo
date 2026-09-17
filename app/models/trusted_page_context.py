"""Trusted page context models for Laravel→RAG communication."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_POSITIVE_DECIMAL_ID_RE = re.compile(r"^[1-9]\d{0,18}$")
_MAX_PAGE_RECORDS = 100
_MAX_RECORD_FIELDS = 16
_MAX_FIELD_KEY_LEN = 64
_MAX_FIELD_VALUE_LEN = 512
_MAX_LABEL_LEN = 256
_MAX_TITLE_LEN = 256
_MAX_PROFILE_LEN = 64


class PageRecord(BaseModel):
    """Laravel-built trusted row visible to the model (no URLs or auth predicates)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=_MAX_LABEL_LEN)
    fields: dict[str, str | None] = Field(default_factory=dict)
    link_key: str = Field(min_length=1, max_length=64)
    truncated_fields: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _positive_decimal_id(cls, value: str) -> str:
        if not _POSITIVE_DECIMAL_ID_RE.fullmatch(value):
            raise ValueError("id must be a positive decimal string")
        return value

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, value: dict[str, str | None]) -> dict[str, str | None]:
        if len(value) > _MAX_RECORD_FIELDS:
            raise ValueError(f"fields exceeds maximum of {_MAX_RECORD_FIELDS}")
        for key, raw in value.items():
            if not isinstance(key, str) or not key or len(key) > _MAX_FIELD_KEY_LEN:
                raise ValueError("invalid field key")
            if raw is not None and len(raw) > _MAX_FIELD_VALUE_LEN:
                raise ValueError(f"field {key!r} exceeds max length")
        return value

    @field_validator("truncated_fields")
    @classmethod
    def _bounded_truncated(cls, value: list[str]) -> list[str]:
        if len(value) > _MAX_RECORD_FIELDS:
            raise ValueError("truncated_fields too long")
        for name in value:
            if not name or len(name) > _MAX_FIELD_KEY_LEN:
                raise ValueError("invalid truncated field name")
        return value


class TrustedPageContext(BaseModel):
    """Laravel→RAG trusted page records (digestless service boundary)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=100)
    kind: str = Field(min_length=1, max_length=32)
    resource_type: str = Field(min_length=1, max_length=64)
    profile: str = Field(min_length=1, max_length=_MAX_PROFILE_LEN)
    title: str = Field(min_length=1, max_length=_MAX_TITLE_LEN)
    records: list[PageRecord] = Field(default_factory=list)

    @field_validator("records")
    @classmethod
    def _bounded_unique_records(cls, value: list[PageRecord]) -> list[PageRecord]:
        if len(value) > _MAX_PAGE_RECORDS:
            raise ValueError(f"records exceeds maximum of {_MAX_PAGE_RECORDS}")
        seen: set[str] = set()
        for record in value:
            if record.id in seen:
                raise ValueError(f"duplicate record id: {record.id}")
            seen.add(record.id)
        return value
