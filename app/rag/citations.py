"""Deterministic citation-marker parsing for semantic RAG answers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from langchain_core.documents import Document

from app.models.schemas import CitationsPayload, CitedSourceRef, Source

_MARKER_RE = re.compile(r"\[Source\s+(\d+)\s*\]", re.IGNORECASE)
_CITATION_LIKE_RE = re.compile(r"\[\s*Source\b[^\]]*\]", re.IGNORECASE)
_MATERIAL_CLAIM_RE = re.compile(
    r"\d|\b(price|cost|policy|procedure|must|should|hours?|open|close)\b", re.IGNORECASE
)
UNVERIFIABLE_CITATION_LIMITATION = "I can't verify this answer against the retrieved sources."


@dataclass(frozen=True)
class CitationFinalization:
    """Canonical answer/citation state shared by JSON and SSE serializers."""

    answer: str
    markers: list[int]
    citations: CitationsPayload
    invalid_markers: int
    unbound_markers: int
    missing_bindings: int
    repair_attempted: bool


def display_name(source_file: str) -> str:
    """Filename-only label for a corpus source path (handles Windows separators)."""
    normalized = source_file.replace("\\", "/")
    name = PurePosixPath(normalized).name
    return name or source_file


def stable_source_id(doc: Document, index: int) -> str:
    """Stable wire id: source path plus chunk_index when present, else enumerate index."""
    source_file = str(doc.metadata.get("source", "unknown"))
    chunk_index = doc.metadata.get("chunk_index")
    if chunk_index is not None:
        return f"{source_file}:{chunk_index}"
    return f"{source_file}:{index}"


def parse_citation_markers(answer: str, n_docs: int) -> list[int]:
    """1-based marker numbers cited in the answer, first-mention order, deduped."""
    seen: set[int] = set()
    markers: list[int] = []
    for match in _MARKER_RE.finditer(answer):
        marker = int(match.group(1))
        if marker < 1 or marker > n_docs or marker in seen:
            continue
        seen.add(marker)
        markers.append(marker)
    return markers


def filter_cited_documents(answer: str, docs: list[Document]) -> tuple[list[Document], bool]:
    markers = parse_citation_markers(answer, len(docs))
    if not markers:
        return docs, False
    return [docs[m - 1] for m in markers], True


def filter_sources_by_markers(sources: list[Source], markers: list[int]) -> list[Source]:
    marker_set = set(markers)
    return [s for s in sources if s.marker is not None and s.marker in marker_set]


def build_citations_payload(
    *,
    sources: list[dict],
    markers: list[int],
    parsed: bool,
) -> CitationsPayload:
    """Marker-bound citation wire shape for JSON/SSE parity."""
    if not parsed:
        return CitationsPayload(parsed=False, cited=[])
    id_by_marker: dict[int, str] = {}
    for position, source in enumerate(sources, start=1):
        marker = source.get("marker", position)
        source_id = source.get("id")
        if (
            isinstance(marker, int)
            and not isinstance(marker, bool)
            and marker >= 1
            and isinstance(source_id, str)
            and source_id
        ):
            id_by_marker[marker] = source_id
    cited = [CitedSourceRef(marker=m, id=id_by_marker[m]) for m in markers if m in id_by_marker]
    return CitationsPayload(parsed=True, cited=cited)


def _source_value(source: Source | Mapping[str, object], key: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)


def _source_bindings(sources: Sequence[Source | Mapping[str, object]]) -> dict[int, str]:
    bindings: dict[int, str] = {}
    for position, source in enumerate(sources):
        marker = _source_value(source, "marker")
        source_id = _source_value(source, "id")
        if not isinstance(marker, int) or isinstance(marker, bool) or marker < 1:
            continue
        if not isinstance(source_id, str) or not source_id:
            source_file = _source_value(source, "source_file")
            chunk_index = _source_value(source, "chunk_index")
            if not isinstance(source_file, str) or not source_file:
                continue
            source_id = f"{source_file}:{chunk_index if chunk_index is not None else position}"
        bindings[marker] = source_id
    return bindings


def _inspect_markers(answer: str, bindings: Mapping[int, str]) -> tuple[list[int], int, int]:
    markers: list[int] = []
    seen: set[int] = set()
    invalid = 0
    unbound = 0
    max_marker = max(bindings, default=0)
    for match in _CITATION_LIKE_RE.finditer(answer):
        numeric_match = _MARKER_RE.fullmatch(match.group(0))
        if numeric_match is None:
            invalid += 1
            continue
        marker = int(numeric_match.group(1))
        if marker < 1 or marker > max_marker:
            invalid += 1
            continue
        if marker not in bindings:
            unbound += 1
            continue
        if marker not in seen:
            seen.add(marker)
            markers.append(marker)
    return markers, invalid, unbound


def _strip_unbound_markers(answer: str, bindings: Mapping[int, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        numeric_match = _MARKER_RE.fullmatch(match.group(0))
        if numeric_match is None:
            return ""
        marker = int(numeric_match.group(1))
        return match.group(0) if marker in bindings else ""

    return _CITATION_LIKE_RE.sub(replace, answer)


def finalize_citations(
    answer: str,
    sources: Sequence[Source | Mapping[str, object]],
    *,
    repair: Callable[[str], str] | None = None,
) -> CitationFinalization:
    """Return one validated answer/citation tuple without unbound markers.

    Fails closed on BAD markers, never on MISSING ones. An invalid or unbound
    marker is stripped so it can never reach the user -- that is what the
    hard-zero gate on unbound or out-of-range citation markers requires. An
    answer that carries no marker releases no unbound marker and so trips no
    gate: it is returned unchanged.

    Missing citations remain measured -- ``missing_bindings`` is still
    reported to telemetry, and a caller-supplied repair is still attempted --
    but they are a quality signal, not a runtime refusal.
    """
    bindings = _source_bindings(sources)
    markers, invalid, unbound = _inspect_markers(answer, bindings)
    # Telemetry-only signal: "sources existed, the answer made a material
    # claim, and cited nothing". Still worth counting, and still worth one
    # bounded repair attempt -- but not a reason to withhold the answer.
    missing_bindings = int(
        bool(bindings)
        and not markers
        and _CITATION_LIKE_RE.search(answer) is None
        and _MATERIAL_CLAIM_RE.search(answer) is not None
    )
    repair_attempted = invalid + unbound + missing_bindings > 0
    finalized_answer = answer

    if repair_attempted:
        repaired = (
            repair(answer) if repair is not None else _strip_unbound_markers(answer, bindings)
        )
        if isinstance(repaired, str):
            finalized_answer = repaired
            markers, _, _ = _inspect_markers(finalized_answer, bindings)

    # Only a genuinely BAD marker reaches this branch. Note missing_bindings is
    # deliberately absent: a zero-marker answer has nothing to strip and
    # nothing to fail closed on.
    if invalid or unbound:
        finalized_answer = _strip_unbound_markers(finalized_answer, bindings)
        markers, _, _ = _inspect_markers(finalized_answer, bindings)

    citations = CitationsPayload(
        parsed=bool(markers),
        cited=[CitedSourceRef(marker=marker, id=bindings[marker]) for marker in markers],
    )
    return CitationFinalization(
        answer=finalized_answer,
        markers=markers,
        citations=citations,
        invalid_markers=invalid,
        unbound_markers=unbound,
        missing_bindings=missing_bindings,
        repair_attempted=repair_attempted,
    )
