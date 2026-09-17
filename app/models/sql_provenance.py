"""SQL execution and record link provenance models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.business_query.outcomes import RecordPreview


class RecordLink(BaseModel):
    table: str
    record_id: int
    label: str = Field(description="Human-readable name as used in the answer")
    url: str = Field(description="Relative app route, minted server-side from the whitelist")
    preview: RecordPreview | None = Field(default=None, description="Optional rich hover card data")


class QueryExplanation(BaseModel):
    """Plain-English interpretation of executed BusinessQuery plan and filters."""

    model_config = ConfigDict(extra="forbid")

    plain_english: str = Field(description="Natural language summary of the query purpose")
    applied_filters: list[str] = Field(
        default_factory=list, description="List of user-facing active filters"
    )
    matched_records_count: int = Field(ge=0, description="Exact number of matching parent entities")
    verified_badge: bool = Field(
        default=True, description="True when query passed BQ formal AST compiler"
    )
    execution_time_ms: int | None = Field(
        default=None, ge=0, description="Elapsed SQL execution latency"
    )


class SqlProvenance(BaseModel):
    queries: list[str] = Field(default_factory=list)
    record_links: list[RecordLink] = Field(default_factory=list)
    explanation: QueryExplanation | None = Field(
        default=None, description="User-facing query explanation"
    )
