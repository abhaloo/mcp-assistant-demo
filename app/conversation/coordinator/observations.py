"""Observation projection and safe business/document presentation for coordinator turns (R5)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.business_query.outcomes import ResultColumn, UnifiedResultEnvelope
from app.business_query.wire.ask_result import AskBusinessQueryResult, CommittedBqResult
from app.conversation.coordinator.action_lifecycle import ActionOutcome
from app.conversation.coordinator.contracts import (
    Observation,
    ObservedColumn,
    ObservedTable,
    ValueRef,
)
from app.conversation.coordinator.draft_checks import stood_on_documents
from app.conversation.followup_context import SelectedSources
from app.rag.retrieval.document_contracts import DocumentFailure, DocumentSearchResult

MAX_OBSERVATION_ROWS = 20
MAX_OBSERVATION_VALUES = 16
SEARCH_SUMMARY_LIMIT = 2000

_SQL_CLAUSE_RE = re.compile(
    r"(?i)\bselect\b.*?\bfrom\b\s+[a-zA-Z0-9_.]+(?:\s+where\b.*?)?|"
    r"\bselect\b.*|"
    r"\bwhere:\s*select\b.*|"
    r"\bbilling_[a-zA-Z0-9_]+\b"
)


def search_summary_line_parts(passage: Any) -> tuple[str, str]:
    """The id/file prefix and the passage content of one search-summary line.

    Both the summary the model reads and the offer text that sits inside it are
    built from these parts, so the prefix and its length have one source.
    """
    return f"[{passage.id}] ({passage.source_file}): ", passage.content


def document_search_summary(passages: Sequence[Any]) -> str:
    """The search observation the model reads, capped at SEARCH_SUMMARY_LIMIT."""
    if not passages:
        return "No documents matched."
    lines = []
    for passage in passages:
        prefix, content = search_summary_line_parts(passage)
        lines.append(f"{prefix}{content}")
    return "\n".join(lines)[:SEARCH_SUMMARY_LIMIT]


def document_search_offer_text(passages: Sequence[Any]) -> str:
    """Passage content that sits inside document_search_summary.

    The id and file-name prefix never enter this string, so a label in a file
    name cannot become an offer.
    """
    if not passages:
        return ""
    chunks: list[str] = []
    cursor = 0
    for index, passage in enumerate(passages):
        if index:
            cursor += 1
            if cursor >= SEARCH_SUMMARY_LIMIT:
                break
        prefix, content = search_summary_line_parts(passage)
        content_start = cursor + len(prefix)
        line_end = content_start + len(content)
        visible_from = max(content_start, 0)
        visible_to = min(line_end, SEARCH_SUMMARY_LIMIT)
        if visible_to > visible_from:
            start = visible_from - content_start
            end = visible_to - content_start
            chunks.append(content[start:end])
        cursor = line_end
        if cursor >= SEARCH_SUMMARY_LIMIT:
            break
    return "\n".join(chunks)


def _json_scalar(value: Any) -> str | int | float | bool | None:
    """Every cell reaches the model as a JSON scalar; a missing value is an explicit null."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _scrub_sql(text: str) -> str:
    return _SQL_CLAUSE_RE.sub("[redacted]", text).strip()


def project_observations(outcomes: Sequence[ActionOutcome]) -> tuple[Observation, ...]:
    """Project completed action outcomes into business-safe Observation objects.

    Preserves completion order exactly. Strips raw SQL and internal identifiers,
    maps formatted values into ValueRef slots, and caps display rows by MAX_OBSERVATION_ROWS.
    """
    observations: list[Observation] = []

    for outcome in outcomes:
        if outcome.status == "succeeded":
            obs = _project_succeeded_outcome(outcome)
        else:
            obs = _project_failed_outcome(outcome)
        observations.append(obs)

    return tuple(observations)


@dataclass(frozen=True)
class _EnvelopeProjection:
    values: tuple[ValueRef, ...]
    columns: tuple[ObservedColumn, ...]
    rows: tuple[dict[str, str | int | float | bool | None], ...]
    row_identity: Any
    summary_parts: list[str]
    truncated: bool
    tables: tuple[ObservedTable, ...] = ()


def _presentation_summary(env: Any, *, seen: int) -> list[str]:
    """The table's heading parts. ``seen`` is how many of its rows the coordinator
    reads; when the row cap cuts the table, the heading says so."""
    parts: list[str] = []
    if env.presentation and env.presentation.title:
        parts.append(_scrub_sql(env.presentation.title))
    if env.presentation and env.presentation.applied_filters:
        clean_filters = [_scrub_sql(str(f)) for f in env.presentation.applied_filters]
        parts.append(f"Filters: {', '.join(clean_filters)}")
    if env.presentation and env.presentation.scope:
        parts.append(f"Scope: {_scrub_sql(env.presentation.scope)}")
    held = len(env.rows or [])
    if env.result_completeness == "partial":
        parts.append(f"shows {env.returned_row_count} of {env.total_row_count} rows")
    elif seen == held:
        parts.append("shows every matching row")
    if seen < held:
        parts.append(f"you see {seen} of its {held} rows")
    return parts


def _fair_shares(sizes: Sequence[int], cap: int) -> list[int]:
    """Split ``cap`` slots across tables so that no table gets zero while it has items.

    Each table first takes an even share (at least one slot), then the room left
    goes to the tables in order. The shares never add up to more than ``cap``.
    """
    even = max(1, cap // len(sizes)) if sizes else 0
    shares: list[int] = []
    room = cap
    for size in sizes:
        take = min(size, even, room)
        shares.append(take)
        room -= take
    for index, size in enumerate(sizes):
        extra = min(size - shares[index], room)
        shares[index] += extra
        room -= extra
    return shares


def _shown_columns(env: UnifiedResultEnvelope) -> list[ResultColumn]:
    """A record id the table shows through its number (display_key) stays hidden:
    the person sees the number, so the model quotes the number."""
    return [c for c in env.columns or () if not (c.is_identifier and c.display_key)]


def _dumped(identity: Any) -> Any:
    return identity.model_dump() if hasattr(identity, "model_dump") else identity


def _value_cells(rows: Sequence[Any], shown: Sequence[Any]) -> list[tuple[str, Any]]:
    """Every non-null cell of the rows, row by row, as (column key, value)."""
    return [(c.key, val) for r in rows for c in shown if (val := r.get(c.key)) is not None]


def _project_envelopes(
    envelopes: Sequence[UnifiedResultEnvelope], *, answer_text: str = ""
) -> _EnvelopeProjection:
    summary_parts: list[str] = []
    columns: list[ObservedColumn] = []
    keyed_rows: list[dict[str, str | int | float | bool | None]] = []
    tables: list[ObservedTable] = []
    identity = None

    # The row cap and the value cap are shared fairly, so a later table keeps its rows.
    row_shares = _fair_shares([len(env.rows or []) for env in envelopes], MAX_OBSERVATION_ROWS)
    kept = [(env.rows or [])[:share] for env, share in zip(envelopes, row_shares, strict=True)]
    shown_columns = [_shown_columns(env) for env in envelopes]
    cells = [_value_cells(r, s) for r, s in zip(kept, shown_columns, strict=True)]
    value_shares = _fair_shares([len(c) for c in cells], MAX_OBSERVATION_VALUES)
    chosen = [cell for c, share in zip(cells, value_shares, strict=True) for cell in c[:share]]
    values = [
        ValueRef(id=f"v{n}", label=key, formatted=str(val))
        for n, (key, val) in enumerate(chosen, start=1)
    ]
    truncated = any(len(r) < len(env.rows or []) for r, env in zip(kept, envelopes, strict=True))

    for index, (env, rows, shown) in enumerate(
        zip(envelopes, kept, shown_columns, strict=True), start=1
    ):
        for c in shown:
            if all(existing.key != c.key for existing in columns):
                columns.append(
                    ObservedColumn(
                        key=c.key,
                        kind=c.value_kind,
                        identifier=c.is_identifier,
                        label=c.label,
                    )
                )
        keyed_rows.extend({c.key: _json_scalar(r.get(c.key)) for c in shown} for r in rows)
        table_identity = getattr(env, "row_identity", None)
        identity = table_identity if identity is None else identity

        heading = "; ".join(_presentation_summary(env, seen=len(rows)))
        summary_parts.append(heading)
        tables.append(
            ObservedTable(
                heading=f"table {index} of {len(envelopes)}: {heading}"[:600],
                rows=len(rows),
                record_counts=_dumped(table_identity),
            )
        )

    if answer_text:
        summary_parts.insert(0, _scrub_sql(answer_text))

    return _EnvelopeProjection(
        tuple(values),
        tuple(columns),
        tuple(keyed_rows),
        _dumped(identity),
        summary_parts,
        truncated,
        tuple(tables),
    )


def _project_query_business(outcome: ActionOutcome, res: Any) -> Observation:
    if isinstance(res, CommittedBqResult):
        res = res.result

    envelopes = []
    if isinstance(res, AskBusinessQueryResult) and res.business_query:
        bq = res.business_query
        envelopes = list(bq.envelopes) if bq.envelopes else ([bq.envelope] if bq.envelope else [])

    answer_text = res.answer_text if isinstance(res, AskBusinessQueryResult) else ""
    proj = _project_envelopes(envelopes, answer_text=answer_text)
    summary = "\n".join(proj.summary_parts) if proj.summary_parts else "Business query completed."
    return Observation(
        action_id=outcome.action_id,
        kind=outcome.kind,
        status=outcome.status,
        evidence_ids=(outcome.action_id,),
        values=proj.values,
        columns=proj.columns,
        rows=proj.rows,
        row_identity=proj.row_identity,
        tables=proj.tables,
        summary=summary[:2000],
        truncated=proj.truncated,
    )


def _project_search_documents(outcome: ActionOutcome, res: Any) -> Observation:
    evidence_ids: tuple[str, ...] = ()
    truncated = False
    passages: Sequence[Any] = ()

    if isinstance(res, DocumentSearchResult):
        evidence_ids = tuple(p.id for p in res.passages)
        truncated = res.truncated
        passages = res.passages

    page_keys = ()
    page_labels = ()
    if isinstance(res, DocumentSearchResult):
        page_keys = tuple(d.key for d in res.page_offers)
        page_labels = tuple(d.label for d in res.page_offers)
    return Observation(
        action_id=outcome.action_id,
        kind=outcome.kind,
        status=outcome.status,
        evidence_ids=evidence_ids,
        summary=document_search_summary(passages),
        truncated=truncated,
        page_keys=page_keys,
        page_labels=page_labels,
    )


def _project_explain_sources(outcome: ActionOutcome, res: Any) -> Observation:
    evidence_ids: tuple[str, ...] = ()
    turns_summary: list[str] = []
    values: list[ValueRef] = []
    rows: list[dict[str, str | int | float | bool | None]] = []
    columns: tuple[ObservedColumn, ...] = ()
    identity, truncated = None, False

    if isinstance(res, SelectedSources):
        evidence_ids = res.exchange_ids
        for t in res.turns:
            title = (
                _scrub_sql(t.presentation.title)
                if (t.presentation and t.presentation.title)
                else ""
            )
            turns_summary.append(f"Restored [{t.exchange_id}] {title}: {t.answer_text}")

            if t.business_query is not None:
                bq = t.business_query
                envs = (
                    list(bq.envelopes) if bq.envelopes else ([bq.envelope] if bq.envelope else [])
                )
                proj = _project_envelopes(envs, answer_text="")
                columns = columns or proj.columns
                identity = identity if identity is not None else proj.row_identity
                space = MAX_OBSERVATION_ROWS - len(rows)
                truncated = truncated or proj.truncated or len(proj.rows) > space
                rows.extend(proj.rows[:space])
                for v in proj.values:
                    if len(values) < MAX_OBSERVATION_VALUES:
                        values.append(
                            ValueRef(id=f"v{len(values) + 1}", label=v.label, formatted=v.formatted)
                        )
                turns_summary.extend(proj.summary_parts)

    summary = "\n".join(turns_summary) if turns_summary else "No sources restored."
    document_answers = (
        tuple(t.exchange_id for t in res.turns if stood_on_documents(t))
        if isinstance(res, SelectedSources)
        else ()
    )
    return Observation(
        action_id=outcome.action_id,
        kind=outcome.kind,
        status=outcome.status,
        evidence_ids=evidence_ids,
        values=tuple(values),
        columns=columns,
        rows=tuple(rows),
        row_identity=identity,
        summary=summary[:2000],
        truncated=truncated,
        document_answers=document_answers,
    )


def _project_succeeded_outcome(outcome: ActionOutcome) -> Observation:
    res: Any = outcome.result
    if outcome.kind == "query_business":
        return _project_query_business(outcome, res)
    if outcome.kind == "search_documents":
        return _project_search_documents(outcome, res)
    if outcome.kind == "explain_sources":
        return _project_explain_sources(outcome, res)
    return Observation(
        action_id=outcome.action_id,
        kind=outcome.kind,
        status=outcome.status,
        summary="",
    )


def _project_failed_outcome(outcome: ActionOutcome) -> Observation:
    res = outcome.result
    code = outcome.status

    if isinstance(res, AskBusinessQueryResult):
        # The model reads the class, then the words.
        code = res.sql_stop_reason or res.answer_text or code
    elif isinstance(res, DocumentFailure) and res.code:
        code = res.code
    elif isinstance(res, Exception):
        code = type(res).__name__

    summary = f"Action {outcome.action_id} {outcome.status}: {code}"
    failure = res.failure_note if isinstance(res, AskBusinessQueryResult) else None
    return Observation(
        action_id=outcome.action_id,
        kind=outcome.kind,
        status=outcome.status,
        failure=failure,
        summary=summary,
        truncated=False,
    )
