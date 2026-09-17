"""Page context v2 identity and capability payload schemas."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_POSITIVE_DECIMAL_ID_RE = re.compile(r"^[1-9]\d{0,18}$")
_MAX_PAGE_RECORDS = 100
_MAX_LABEL_LEN = 256
_MAX_TITLE_LEN = 256
_MAX_PROFILE_LEN = 64
_BUNDLE_HASH_PATTERN = r"^sha256:[0-9a-fA-F]{64}$"


class RecordIdentity(BaseModel):
    """Authorized record identity on page context v2 (no detail values required)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=20)
    label: str | None = Field(default=None, max_length=_MAX_LABEL_LEN)
    resource_type: str = Field(default="work_order", min_length=1, max_length=64)
    link_key: str | None = Field(default=None, max_length=64)

    @field_validator("id")
    @classmethod
    def _positive_decimal_id(cls, value: str) -> str:
        if not _POSITIVE_DECIMAL_ID_RE.fullmatch(value):
            raise ValueError("id must be a positive decimal string")
        return value


class PageContextV2(BaseModel):
    """Version 2 page context identity and capability payload."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    profile: str = Field(min_length=1, max_length=_MAX_PROFILE_LEN)
    records: list[RecordIdentity] = Field(default_factory=list)
    detail_contract: str = Field(default="v2", min_length=1, max_length=64)
    definition_bundle_hash: Annotated[str, Field(pattern=_BUNDLE_HASH_PATTERN)]
    compatibility_epoch: int = Field(default=1, ge=1)
    kind: str = Field(default="detail", min_length=1, max_length=32)
    resource_type: str = Field(default="work_order", min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=_MAX_TITLE_LEN)

    @field_validator("records")
    @classmethod
    def _bounded_unique_records(cls, value: list[RecordIdentity]) -> list[RecordIdentity]:
        if len(value) > _MAX_PAGE_RECORDS:
            raise ValueError(f"records exceeds maximum of {_MAX_PAGE_RECORDS}")
        seen: set[str] = set()
        for record in value:
            if record.id in seen:
                raise ValueError(f"duplicate record id: {record.id}")
            seen.add(record.id)
        return value
