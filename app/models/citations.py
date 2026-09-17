"""Citation and source provenance models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Source(BaseModel):
    """A source document chunk that contributed to the answer."""

    id: str | None = Field(
        default=None,
        description="Stable source identifier used by canonical citation bindings.",
    )
    content: str = Field(description="The text content of the source chunk")
    source_file: str = Field(description="Filename or path of the source document")
    chunk_index: int | None = Field(default=None, description="Chunk position within its document")
    marker: int | None = Field(
        default=None,
        description="[Source N] number used in the answer; null when citation parsing fell back",
    )
    section: str | None = Field(
        default=None,
        description="Heading path within the document, when ingestion provides it",
    )
    resource_type: str | None = Field(
        default=None,
        description="Record resource type from the validated record context, e.g. 'quotation'",
    )
    record_id: str | None = Field(
        default=None,
        description="Record id from the validated record context",
    )
    label: str | None = Field(
        default=None,
        description="Record display label from the validated record context",
    )
    link_key: str | None = Field(
        default=None,
        description="Record link key from the validated record context, for client-side routing",
    )


class CitedSourceRef(BaseModel):
    """Marker-bound citation reference for SSE/JSON parity."""

    marker: int = Field(ge=1)
    id: str = Field(min_length=1, max_length=256)


class CitationsPayload(BaseModel):
    parsed: bool
    cited: list[CitedSourceRef] = Field(default_factory=list)
