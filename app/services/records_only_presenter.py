"""Deterministic, localized presentation of authorized page records."""

from __future__ import annotations

import re

from app.models.schemas import PageRecord, TrustedPageContext
from app.rag.provenance.record_links import extract_record_links_from_page_records
from app.services.follow_up_suggestions import normalize_follow_up_suggestions
from app.services.records_only_intents import classify_records_intent
from app.services.records_only_types import (
    PriorityFilter,
    RecordsIntent,
    RecordsOnlyResult,
    ResponseLanguage,
)

_SWAHILI_SUGGESTION_RE = re.compile(
    r"\b(?:nionyeshe|onyesha|orodhesha|panga|linganisha|taja|eleza|nipe|"
    r"kazi|kipaumbele|tarehe|mteja|wateja|hali|idara)\b",
    re.IGNORECASE,
)


def insufficient_answer(language: ResponseLanguage) -> str:
    if language == "sw":
        return (
            "Ninaweza kujibu tu kwa kutumia kazi zinazoonekana kwenye ukurasa huu. "
            "Ondoa kadi ya muktadha ili kutafuta nyaraka na hifadhidata, au badilisha "
            "orodha kisha uulize tena."
        )
    return (
        "I can only answer from the jobs currently shown on this page. "
        "Open the context card and remove it to search documents and the database, "
        "or adjust the list and ask again."
    )


def _default_suggestions(language: ResponseLanguage) -> list[str]:
    if language == "sw":
        return [
            "Nionyeshe tarehe za kukabidhi kazi hizi",
            "Panga kazi hizi kwa mteja",
            "Linganisha hali za kazi hizi",
        ]
    return [
        "Show the delivery dates for these jobs",
        "Group these jobs by customer",
        "Compare the statuses of these jobs",
    ]


def language_consistent_suggestions(
    suggestions: list[str],
    language: ResponseLanguage,
) -> list[str]:
    normalized = normalize_follow_up_suggestions(suggestions)
    consistent = [
        suggestion
        for suggestion in normalized
        if bool(_SWAHILI_SUGGESTION_RE.search(suggestion)) == (language == "sw")
    ]
    return normalize_follow_up_suggestions(consistent + _default_suggestions(language))


def _is_truthy_field(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _is_high_priority(record: PageRecord) -> bool:
    display_value = record.fields.get("is_high_priority")
    if display_value is not None:
        return _is_truthy_field(display_value)
    return str(record.fields.get("priority") or "").strip().casefold() == "high"


def _links_result(
    answer: str,
    records: list[PageRecord],
    cited: list[str],
    suggestions: list[str],
) -> RecordsOnlyResult:
    return RecordsOnlyResult(
        answer=answer,
        cited_record_ids=cited,
        record_links=extract_record_links_from_page_records(records, cited),
        follow_up_suggestions=suggestions,
    )


def _count_suggestions(
    language: ResponseLanguage,
    priority_filter: PriorityFilter | None,
) -> list[str]:
    if language == "sw":
        if priority_filter is not None:
            priority = "juu" if priority_filter == "high" else "kawaida"
            return [
                f"Nionyeshe maelezo ya kazi zenye kipaumbele cha {priority}",
                "Panga kazi hizi kwa mteja",
                "Linganisha hali za kazi hizi",
            ]
        return [
            "Orodhesha kazi zote kwenye ukurasa huu",
            "Onyesha kazi zenye kipaumbele cha juu",
            "Panga kazi hizi kwa mteja",
        ]

    if priority_filter is not None:
        priority = "high" if priority_filter == "high" else "normal"
        return [
            f"Show details for the {priority}-priority jobs",
            "Group these jobs by customer",
            "Compare their statuses",
        ]
    return [
        "List every job on this page",
        "Show only high-priority jobs",
        "Group these jobs by customer",
    ]


def _count_answer(intent: RecordsIntent, records: list[PageRecord]) -> RecordsOnlyResult:
    if intent.priority_filter == "high":
        matched = [record for record in records if _is_high_priority(record)]
    elif intent.priority_filter == "normal":
        matched = [record for record in records if not _is_high_priority(record)]
    else:
        matched = records

    count = len(matched)
    total = len(records)
    if intent.language == "sw":
        if intent.priority_filter is not None:
            priority = (
                "kipaumbele cha juu"
                if intent.priority_filter == "high"
                else "kipaumbele cha kawaida"
            )
            counted_jobs = "kazi 1 yenye" if count == 1 else f"kazi {count} zenye"
            answer = (
                f"Kuna {counted_jobs} {priority} kati ya kazi {total} "
                "zinazoonekana kwenye ukurasa huu."
            )
        else:
            answer = f"Kuna kazi {count} zinazoonekana kwenye ukurasa huu."
    else:
        noun = "job"
        if intent.priority_filter is not None:
            noun = f"{intent.priority_filter}-priority job"
        verb = "is" if count == 1 else "are"
        pluralized_noun = noun if count == 1 else f"{noun}s"
        if intent.priority_filter is not None:
            total_noun = "job" if total == 1 else "jobs"
            answer = (
                f"There {verb} {count} {pluralized_noun} among the {total} "
                f"{total_noun} currently shown on this page."
            )
        else:
            answer = f"There {verb} {count} {pluralized_noun} currently shown on this page."

    cited = [record.id for record in matched]
    return _links_result(
        answer,
        records,
        cited,
        _count_suggestions(intent.language, intent.priority_filter),
    )


def _clean_display(value: object, fallback: str = "Unknown") -> str:
    cleaned = " ".join(str(value or "").split())
    return cleaned or fallback


def _job_line(
    record: PageRecord,
    *,
    include_customer: bool,
    language: ResponseLanguage,
) -> str:
    fields = record.fields
    work_number = _clean_display(fields.get("work_number"), record.id)
    title = _clean_display(fields.get("title"), "Untitled job")
    details: list[str] = []
    if include_customer:
        details.append(_clean_display(fields.get("customer"), "Unknown customer"))
    for field in ("status", "department"):
        value = _clean_display(fields.get(field), "")
        if value:
            details.append(value)
    if _is_high_priority(record):
        details.append("kipaumbele cha juu" if language == "sw" else "high priority")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"#{work_number} — {title}{suffix}"


def _top_priority_answer(intent: RecordsIntent, records: list[PageRecord]) -> RecordsOnlyResult:
    high_priority = [record for record in records if _is_high_priority(record)]
    candidates = high_priority or records
    priority_label = "High" if high_priority else "Normal"
    cited = [record.id for record in candidates]

    if len(candidates) == 1:
        candidate = candidates[0]
        label = _clean_display(candidate.label, candidate.id)
        title = _clean_display(candidate.fields.get("title"), "Untitled job")
        if intent.language == "sw":
            answer = (
                f"{label} — {title} ndiyo kazi pekee yenye kipaumbele cha juu zaidi "
                "kwenye ukurasa huu."
            )
            suggestions = [
                "Onyesha tarehe ya kukabidhi kazi hii",
                "Onyesha hali ya kazi hii",
                "Onyesha kazi nyingine za mteja huyu",
            ]
        else:
            answer = (
                f"{label} — {title} is the only job at the highest available priority "
                f"({priority_label}) on this page."
            )
            suggestions = [
                "Show this job's delivery date",
                "Show this job's status",
                "Show other jobs for this customer",
            ]
        return _links_result(answer, records, cited, suggestions)

    displayed = candidates[:5]
    candidate_lines = "\n".join(
        f"- {_clean_display(record.label, record.id)} — "
        f"{_clean_display(record.fields.get('title'), 'Untitled job')}"
        for record in displayed
    )
    remaining = len(candidates) - len(displayed)
    if remaining:
        candidate_lines += (
            f"\n- …na kazi nyingine {remaining}"
            if intent.language == "sw"
            else f"\n- …and {remaining} more"
        )

    if intent.language == "sw":
        answer = (
            f"Kuna kazi {len(candidates)} zenye kiwango kilekile cha juu cha "
            "kipaumbele kwenye ukurasa huu:\n"
            f"{candidate_lines}\n\n"
            "Unataka nitumie kigezo gani kuzichagua—tarehe ya kukabidhi, hali, "
            "au tarehe ya kuundwa?"
        )
        suggestions = [
            "Panga kazi hizi kwa tarehe ya kukabidhi",
            "Linganisha hali za kazi hizi",
            "Panga kazi hizi kwa tarehe ya kuundwa",
        ]
    else:
        answer = (
            f"{len(candidates)} jobs are tied at the highest available priority "
            f"({priority_label}) on this page:\n"
            f"{candidate_lines}\n\n"
            "Which criterion should I use to choose among them—delivery date, "
            "status, or creation date?"
        )
        suggestions = [
            "Rank the tied jobs by delivery date",
            "Compare the tied jobs' statuses",
            "Rank the tied jobs by creation date",
        ]
    return _links_result(answer, records, cited, suggestions)


def _markdown_cell(value: object, fallback: str = "—") -> str:
    return _clean_display(value, fallback).replace("|", "\\|")


def _job_table(records: list[PageRecord], language: ResponseLanguage) -> str:
    rows = (
        [
            "| Kazi | Mteja | Hali | Idara | Kipaumbele |",
            "| --- | --- | --- | --- | --- |",
        ]
        if language == "sw"
        else [
            "| Job | Customer | Status | Department | Priority |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for record in records:
        priority = (
            ("Juu" if _is_high_priority(record) else "Kawaida")
            if language == "sw"
            else ("High" if _is_high_priority(record) else "Normal")
        )
        rows.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(record.label),
                    _markdown_cell(record.fields.get("customer")),
                    _markdown_cell(record.fields.get("status")),
                    _markdown_cell(record.fields.get("department")),
                    priority,
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def _overview_suggestions(language: ResponseLanguage) -> list[str]:
    if language == "sw":
        return [
            "Onyesha kazi zenye kipaumbele cha juu",
            "Panga kazi hizi kwa mteja",
            "Linganisha hali za kazi hizi",
        ]
    return [
        "Show only high-priority jobs",
        "Group these jobs by customer",
        "Compare their statuses",
    ]


def _customers_answer(intent: RecordsIntent, records: list[PageRecord]) -> RecordsOnlyResult | None:
    customers = list(
        dict.fromkeys(
            customer
            for record in records
            if (customer := _clean_display(record.fields.get("customer"), ""))
        )
    )
    if not customers:
        return None
    if intent.language == "sw":
        answer = f"Wateja {len(customers)} wanaoonekana kwenye ukurasa huu ni:\n"
    else:
        noun = "customer" if len(customers) == 1 else "customers"
        answer = f"The {len(customers)} {noun} shown on this page are:\n"
    answer += "\n".join(f"- {customer}" for customer in customers)
    cited = [record.id for record in records]
    return _links_result(answer, records, cited, _overview_suggestions(intent.language))


def _overview_answer(intent: RecordsIntent, records: list[PageRecord]) -> RecordsOnlyResult:
    if intent.group_by_customer:
        grouped: dict[str, list[PageRecord]] = {}
        for record in records:
            customer = _clean_display(record.fields.get("customer"), "Unknown customer")
            grouped.setdefault(customer, []).append(record)
        sections = []
        for customer, customer_records in grouped.items():
            lines = "\n".join(
                f"  - {_job_line(record, include_customer=False, language=intent.language)}"
                for record in customer_records
            )
            sections.append(f"- **{customer}**\n{lines}")
        prefix = (
            f"Kazi {len(records)} zinazoonekana sasa, zikipangwa kwa mteja, ni:\n"
            if intent.language == "sw"
            else f"The {len(records)} jobs currently shown, grouped by customer, are:\n"
        )
        answer = prefix + "\n".join(sections)
    else:
        prefix = (
            f"Kazi {len(records)} zinazoonekana sasa ni:\n\n"
            if intent.language == "sw"
            else f"The {len(records)} jobs currently shown are:\n\n"
        )
        answer = prefix + _job_table(records, intent.language)
    cited = [record.id for record in records]
    return _links_result(answer, records, cited, _overview_suggestions(intent.language))


def render_deterministic_answer(
    question: str,
    page_context: TrustedPageContext,
) -> RecordsOnlyResult | None:
    match = classify_records_intent(question)
    if not match.is_complete or match.intent is None:
        return None
    intent = match.intent
    records = list(page_context.records)
    if not records:
        return None
    if intent.kind == "top_priority":
        return _top_priority_answer(intent, records)
    if intent.kind == "count":
        return _count_answer(intent, records)
    if intent.kind == "customers":
        return _customers_answer(intent, records)
    return _overview_answer(intent, records)
