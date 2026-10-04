"""Canonical passage identity and Document conversion."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any

from langchain_core.documents import Document

from app.models.citations import CitationsPayload, Source
from app.rag.citations import stable_source_id

# The most passage text a source card shows, on every wire.
_SNIPPET_CHARS = 500
# Ingestion joins a chunk's headings into its section, the document's H1 first.
_SECTION_SEPARATOR = " > "

# Markdown that a source card must not show. A heading line goes completely,
# because the card shows the section. A ">" inside a line is a menu path in
# the manuals ("Financial Reports" > "Account Statement"), so only a ">" that
# starts a line is a quote marker.
_HEADING_LINE = re.compile(r"^ {0,3}#{1,6}(?:[ \t].*)?$", re.MULTILINE)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LINE_MARKER = re.compile(r"^\s*(?:[-+*]|>)\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"\*\*|__|~~|[*`]")
_TABLE_RULE = re.compile(r"\||[-=]{3,}")
_WHITESPACE = re.compile(r"\s+")


def document_title(section: str | None, source_file: str) -> str:
    """The title of a passage's document: its first heading, else its file name."""
    if isinstance(section, str):
        first_heading = section.split(_SECTION_SEPARATOR, 1)[0].strip()
        if first_heading:
            return first_heading
    return os.path.basename(source_file) or source_file


def plain_snippet(content: str) -> str:
    """The passage as plain text for a source card.

    No markdown marker and one space between words. Text longer than the card
    limit is cut at a word boundary and ends with an ellipsis.
    """
    text = _HEADING_LINE.sub(" ", content)
    text = _LINK.sub(r"\1", text)
    text = _LINE_MARKER.sub(" ", text)
    text = _EMPHASIS.sub("", text)
    text = _TABLE_RULE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if len(text) <= _SNIPPET_CHARS:
        return text
    head = text[: _SNIPPET_CHARS - 1]
    if text[_SNIPPET_CHARS - 1] != " " and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,;:") + "…"


@dataclass(frozen=True)
class RetrievedSource:
    id: str
    content: str
    source_file: str
    title: str | None = None
    access_tier: str | None = None
    chunk_index: int | None = None
    marker: int | None = None
    section: str | None = None
    resource_type: str | None = None
    record_id: int | str | None = None
    label: str | None = None
    link_key: str | None = None


def to_json_source(src: RetrievedSource) -> Source:
    record_id = src.record_id
    if record_id is not None:
        record_id = str(record_id)
    return Source(
        id=src.id,
        content=src.content[:500],
        source_file=src.source_file,
        title=src.title,
        snippet=plain_snippet(src.content),
        chunk_index=src.chunk_index,
        marker=src.marker,
        section=src.section,
        resource_type=src.resource_type,
        record_id=record_id,
        label=src.label,
        link_key=src.link_key,
    )


def to_sse_source(src: RetrievedSource) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": src.id,
        "title": src.title,
        "snippet": plain_snippet(src.content),
        "access_tier": src.access_tier,
        "marker": src.marker,
    }
    if src.resource_type is not None:
        payload.update(
            resource_type=src.resource_type,
            record_id=src.record_id,
            label=src.label,
            link_key=src.link_key,
        )
    return payload


def sources_from_docs(docs: list[Document]) -> list[Source]:
    return [to_json_source(src) for src in retrieved_from_docs(docs)]


def retrieved_from_docs(docs: list[Document]) -> tuple[RetrievedSource, ...]:
    sources: list[RetrievedSource] = []
    for i, doc in enumerate(docs):
        source_file = str(doc.metadata.get("source", "unknown"))
        sources.append(
            RetrievedSource(
                id=stable_source_id(doc, i),
                content=doc.page_content,
                source_file=source_file,
                title=document_title(doc.metadata.get("section"), source_file),
                access_tier=doc.metadata.get("access_tier", "unknown"),
                chunk_index=doc.metadata.get("chunk_index"),
                marker=i + 1,
                section=doc.metadata.get("section"),
            )
        )
    return tuple(sources)


def filter_retrieved(
    sources: tuple[RetrievedSource, ...], citations: CitationsPayload
) -> tuple[RetrievedSource, ...]:
    if citations.parsed:
        markers = {citation.marker for citation in citations.cited}
        return tuple(src for src in sources if src.marker is None or src.marker in markers)
    return tuple(replace(src, marker=None) if src.marker is not None else src for src in sources)


def retrieved_from_sources(sources: list[Source]) -> tuple[RetrievedSource, ...]:
    return tuple(
        RetrievedSource(
            id=src.id or "",
            content=src.content,
            source_file=src.source_file,
            chunk_index=src.chunk_index,
            marker=src.marker,
            section=src.section,
            resource_type=src.resource_type,
            record_id=src.record_id,
            label=src.label,
            link_key=src.link_key,
        )
        for src in sources
    )


def documents_from_passages(passages: tuple[RetrievedSource, ...]) -> list[Document]:
    return [
        Document(
            page_content=src.content,
            metadata={
                "source": src.source_file,
                "chunk_index": src.chunk_index,
                "access_tier": src.access_tier,
                "section": src.section,
            },
        )
        for src in passages
    ]
