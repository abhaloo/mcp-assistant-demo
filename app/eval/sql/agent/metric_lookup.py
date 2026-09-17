"""Billing metric resolver for pre-inject."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

from app.eval.sql.agent.clarify import CLARIFY_PREFIX
from app.rag.metrics.billing_metrics import load_catalog

METRIC_INJECT_MARKER = "Billing metric definition (pre-injected):"

# Hand-tuned intent vocabulary — not eval gold questions (D5c).
# High-precision only: multi-word catalog phrases and alias-proximate terms.
# Bare order(s), pay, balance, and singular invoice are excluded unless paired
# with metric words (e.g. open orders, outstanding balance, invoice total).
_METRIC_INTENT_RE = re.compile(
    r"\b(?:"
    r"fully[\s-]?paid|partial(?:ly)?[\s-]?paid|over[\s-]?paid|"
    r"partial[\s-]payment|paid[\s-]in[\s-]full|"
    r"past[\s-]due|open[\s-]orders?|pending[\s-]orders?|"
    r"customer[\s-]orders?[\s-]open|"
    r"outstanding[\s-]balance|standing[\s-]balance|"
    r"receivable[\s-]balance|total[\s-]receivable|"
    r"invoiced[\s-]?(?:sales|value|revenue)|"
    r"ledger[\s-]revenue|accounting[\s-]revenue|journal[\s-]revenue|"
    r"invoice[\s-]totals?|invoices[\s-]fully[\s-]paid|"
    r"overdue[\s-]invoices?|finished[\s-]unbilled|unbilled[\s-]jobs?|"
    r"jobs?[\s-]not[\s-]invoiced|finished[\s-]jobs?[\s-]without[\s-]invoice|"
    r"amount[\s-]owed|accounts[\s-]receivable|"
    r"payments?[\s-]exceed|"
    r"revenue|overdue|aging|aged|outstanding|unbilled|overpaid|unpaid|"
    r"receivable|receivables|owed|owing|payment|payments|paid"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MetricHit:
    """Unique alias match — definition payload for pre-inject."""

    metric_id: str
    definition: str
    caveats: str
    column_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class SoftAbsent:
    """No metric intent — do not inject or CLARIFY."""

    clarify_required: bool = False


@dataclass(frozen=True)
class ClarifyRequired:
    """Metric intent with zero or ambiguous catalog hits."""

    reason: str
    clarify_required: bool = True
    candidate_ids: tuple[str, ...] = ()


def has_metric_intent(question: str) -> bool:
    """True when the question uses billing metric vocabulary."""
    return bool(_METRIC_INTENT_RE.search(question or ""))


def metric_resolution_question(messages: list[Any]) -> str:
    """Text for metric alias resolution — first Human, or first+last on clarify reply."""
    humans = [str(m.content or "") for m in messages if isinstance(m, HumanMessage)]
    if not humans:
        return ""
    if len(humans) == 1:
        return humans[0]
    return f"{humans[0]} {humans[-1]}"


def _normalize(text: str) -> str:
    return (text or "").casefold()


def _alias_matches(question: str, alias: str) -> bool:
    alias_norm = alias.strip().casefold()
    if not alias_norm:
        return False
    return alias_norm in _normalize(question)


def _longest_matching_alias(question: str, metric: dict) -> str | None:
    matched = [
        str(alias).strip().casefold()
        for alias in metric.get("aliases") or []
        if _alias_matches(question, str(alias))
    ]
    return max(matched, key=len) if matched else None


def _find_matching_metrics(question: str, catalog: list[dict]) -> list[dict]:
    """Metrics whose alias appears in the question, minus those a longer alias subsumes.

    "accounts receivable aging" matches both ar_aging and outstanding (via "accounts
    receivable"). The contained claim loses, so a compound phrase resolves instead of
    reading as ambiguity. Two overlapping-but-unnested claims (overdue vs outstanding)
    both survive and still clarify.
    """
    claims = [(metric, _longest_matching_alias(question, metric)) for metric in catalog]
    claims = [(metric, alias) for metric, alias in claims if alias is not None]
    return [
        metric
        for metric, alias in claims
        if not any(other != alias and alias in other for _, other in claims)
    ]


def resolve_metric(
    question: str, catalog: list[dict] | None = None
) -> MetricHit | SoftAbsent | ClarifyRequired:
    """Resolve a billing metric from natural language (D5c intent-gated)."""
    if not has_metric_intent(question):
        return SoftAbsent()

    metrics = catalog if catalog is not None else load_catalog()
    matches = _find_matching_metrics(question, metrics)

    if not matches:
        return ClarifyRequired(
            reason=(
                "Which billing metric do you mean? For revenue, specify invoiced "
                "sales vs ledger/accounting revenue."
            ),
            candidate_ids=tuple(),
        )

    if len(matches) > 1:
        ids = tuple(m["id"] for m in matches)
        labels = ", ".join(ids)
        return ClarifyRequired(
            reason=f"Which billing metric do you mean: {labels}?",
            candidate_ids=ids,
        )

    metric = matches[0]
    hints = metric.get("column_hints") or []
    return MetricHit(
        metric_id=str(metric["id"]),
        definition=str(metric["definition"]).strip(),
        caveats=str(metric["caveats"]).strip(),
        column_hints=tuple(str(h) for h in hints),
    )


def format_metric_inject_block(hit: MetricHit) -> str:
    """Format the pre-inject definition block appended to system content."""
    lines = [
        METRIC_INJECT_MARKER,
        f"Metric id: {hit.metric_id}",
        f"Definition: {hit.definition}",
        f"Caveats: {hit.caveats}",
    ]
    if hit.column_hints:
        lines.append("Column hints: " + "; ".join(hit.column_hints))
    return "\n".join(lines)


def build_metric_clarify_message(outcome: ClarifyRequired) -> AIMessage:
    """Emit shared CLARIFY marker with no tool_calls (D5c)."""
    return AIMessage(content=f"{CLARIFY_PREFIX} {outcome.reason}")
