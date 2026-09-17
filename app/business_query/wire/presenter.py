"""Answered -> user-facing text (ADR 0047).

Three concerns: (1) ``present_answered``, the deterministic presenter — pure
and total, no model calls, never raises on any ``Answered`` the module can
produce; (2) ``rendered_values_preserved``, the numeric value-preservation
guard that checks a rendered answer never introduces an unverifiable number;
(3) ``render_rich``, an optional one-call LLM prose renderer that falls back
to its caller-provided plan presenter, or ``present_answered`` when none is
given, on any failure. Only (3) makes a model call or imports from the
provider layer.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from math import floor, isfinite, log10
from typing import Any

from langchain_core.messages import HumanMessage

from app.business_query.outcomes import (
    Answered,
    RecordDetail,
    serialize_unified_envelope,
)
from app.business_query.plan import (
    AttributePredicate,
    BusinessQueryPlan,
    CallRecorder,
    FilterGroup,
    iter_filter_leaves,
    plan_member_names,
)
from app.business_query.wire.cell_format import (
    MISSING,
    NO_MATCHING_ROWS,
    column_kinds,
    format_cell_value,
    group_row_label,
)
from app.business_query.wire.explainer import _format_filter
from app.business_query.wire.result_presentation import (
    PLURAL_SUBJECTS,
    _facts,
    _subject,
    column_label,
)

serialize_result_envelope = serialize_unified_envelope


logger = logging.getLogger(__name__)

_MAX_RENDER_CHARS = 8_000

# Plain JSON schema dict, never a strict Pydantic class. One required string
# field. Needs a top-level "title" -- an untitled dict fails
# with_structured_output at bind time on BaseChatOpenAI subclasses.
_RENDER_REPLY_SCHEMA = {
    "title": "render_reply",
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}

# v1: percent-suffixed tokens are excluded — derived percentages are legitimate
# renders we cannot verify cheaply.
_MAGNITUDE_SUFFIXES: dict[str, int] = {
    "thousand": 3,
    "k": 3,
    "million": 6,
    "m": 6,
    "billion": 9,
    "b": 9,
    "trillion": 12,
    "t": 12,
}
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\w.])(?:[A-Z]{3}\s+)?(?P<sign>-)?"
    r"(?P<num>[\d,]+(?:\.\d+)?)"
    r"(?:\s*(?P<mag>thousand|million|billion|trillion|[kmbt]))?"
    # A magnitude word ("billion") is alphabetic, so sentence-final punctuation
    # right after it is unambiguous -- accept it. A bare number's boundary
    # stays exact (no magnitude captured) so a hyphenated range like "31-60"
    # or a date "2024-06-01" is untouched.
    r"(?(mag)(?=[\s.,]|[^\w.,]|$)|(?=\s|[^\w.,]|$))",
    re.IGNORECASE,
)
_HIGH_PRECISION_SIG_FIGS = 6


def rendered_values_preserved(text: str, plan: BusinessQueryPlan, answered: Answered) -> bool:
    """Return whether every numeric token in ``text`` is allowlisted from the answer.

    Allowlist: row cell values, ``total_row_count``, ``len(rows)``, per-measure
    column sums, and plan period years / filter literals. Percent-suffixed tokens
    are skipped in v1. Matching is rounding-aware for magnitude prose
    (e.g. "5.61 billion" matches 5,610,750,957.53).
    """
    allowed = _allowed_numeric_values(plan, answered)
    rows = answered.rows[: plan.limit]
    for numeric_part, magnitude_exp, sig_figs, negative in _extract_numeric_tokens(text):
        token_value = _parse_token_value(numeric_part, magnitude_exp, negative=negative)
        if token_value is None:
            continue
        if _token_matches_allowed(token_value, sig_figs, allowed):
            continue
        if _token_embedded_in_row_label(numeric_part, rows):
            continue
        return False
    return True


def _extract_numeric_tokens(text: str) -> list[tuple[str, int, int, bool]]:
    tokens: list[tuple[str, int, int, bool]] = []
    for match in _NUMERIC_TOKEN_RE.finditer(text):
        start = match.start()
        if start > 0 and text[start - 1] == "%":
            continue
        end = match.end()
        if end < len(text) and text[end] == "%":
            continue
        numeric_part = match.group("num")
        mag = match.group("mag")
        magnitude_exp = _MAGNITUDE_SUFFIXES[mag.lower()] if mag else 0
        negative = match.group("sign") is not None
        tokens.append((numeric_part, magnitude_exp, _count_sig_figs(numeric_part), negative))
    return tokens


def _parse_token_value(
    numeric_part: str, magnitude_exp: int, *, negative: bool = False
) -> float | None:
    cleaned = numeric_part.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if magnitude_exp:
        value *= 10**magnitude_exp
    if negative:
        value = -value
    return value if isfinite(value) else None


def _count_sig_figs(numeric_part: str) -> int:
    s = numeric_part.replace(",", "").strip()
    if not s:
        return 0
    if "." in s:
        left, right = s.split(".", 1)
        left_digits = left.lstrip("0")
        if not left_digits or set(left_digits) == {"0"}:
            stripped = right.lstrip("0")
            return len(stripped) if stripped else 0
        return len(left_digits) + len(right)
    stripped = s.lstrip("0") or "0"
    if stripped == "0":
        return 1
    return len(stripped.rstrip("0"))


def _round_to_sig_figs(value: float, sig_figs: int) -> float:
    if not isfinite(value) or value == 0 or sig_figs <= 0:
        return 0.0
    digits = -int(floor(log10(abs(value)))) + (sig_figs - 1)
    return round(value, digits)


def _token_embedded_in_row_label(numeric_part: str, rows: list[dict]) -> bool:
    """Allow numerals that only appear inside a rendered row value -- a string
    dimension label (e.g. ``31-60``), or a ``date``/``datetime`` value's
    stringified components (e.g. the "2024", "06", "01" of ``2024-06-01``)."""
    needle = numeric_part.replace(",", "")
    if not needle:
        return False
    for row in rows:
        for value in row.values():
            if isinstance(value, str) and needle in value:
                return True
            if isinstance(value, date) and needle in str(value):
                return True
    return False


def _token_matches_allowed(token_value: float, sig_figs: int, allowed: set[float]) -> bool:
    for candidate in allowed:
        if _values_equivalent(token_value, candidate, sig_figs):
            return True
    return False


def _values_equivalent(rendered: float, allowed: float, sig_figs: int) -> bool:
    if rendered == allowed:
        return True
    if sig_figs >= _HIGH_PRECISION_SIG_FIGS:
        tolerance = max(0.01, abs(allowed) * 1e-9)
        return abs(rendered - allowed) <= tolerance
    rounded_rendered = _round_to_sig_figs(rendered, sig_figs)
    rounded_allowed = _round_to_sig_figs(allowed, sig_figs)
    return abs(rounded_rendered - rounded_allowed) <= max(1e-6, abs(rounded_allowed) * 1e-9)


def _allowed_numeric_values(plan: BusinessQueryPlan, answered: Answered) -> set[float]:
    allowed: set[float] = set()
    rows = answered.rows[: plan.limit]
    for row in rows:
        for value in row.values():
            numeric = _coerce_numeric(value)
            if numeric is not None:
                allowed.add(numeric)
    allowed.add(float(answered.total_row_count))
    allowed.add(float(len(rows)))
    for measure in plan.measures:
        total = 0.0
        saw_value = False
        for row in rows:
            numeric = _coerce_numeric(row.get(measure))
            if numeric is not None:
                total += numeric
                saw_value = True
        if saw_value:
            allowed.add(total)
    allowed.update(_plan_literal_values(plan))
    return allowed


def _coerce_numeric(value: object) -> float | None:
    """Best-effort float coercion -- never returns a non-finite value (a row
    literally named "Infinity"/"NaN" must not poison the allowlist and later
    blow up ``_round_to_sig_figs``)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, Decimal):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.replace(",", ""))
        except ValueError:
            return None
    else:
        return None
    return result if isfinite(result) else None


def _plan_literal_values(plan: BusinessQueryPlan) -> set[float]:
    values: set[float] = set()
    if plan.period is not None:
        values.update(_period_years(plan.period))
    for group in (plan.filters, plan.having):
        values.update(_filter_literal_values(group))
    for d in plan.derived_sets:
        if d.mode == "ranked" and d.plan.limit is not None:
            values.add(float(d.plan.limit))
        if d.plan.period is not None:
            values.update(_period_years(d.plan.period))
        for group in (d.plan.filters, d.plan.having):
            values.update(_filter_literal_values(group))
    return values


def _period_years(period: object) -> set[float]:
    years: set[float] = set()
    on = getattr(period, "on", None)
    since = getattr(period, "since", None)
    between = getattr(period, "between", None)
    for candidate in (on, since):
        if isinstance(candidate, date):
            years.add(float(candidate.year))
    if isinstance(between, tuple):
        for candidate in between:
            if isinstance(candidate, date):
                years.add(float(candidate.year))
    return years


def _filter_literal_values(group: FilterGroup | None) -> set[float]:
    if group is None:
        return set()
    values: set[float] = set()
    for leaf in iter_filter_leaves(group):
        if getattr(leaf, "operator", None) in ("in_set", "not_in_set"):
            continue
        for literal in leaf.values:
            numeric = _coerce_numeric(literal)
            if numeric is not None:
                values.add(numeric)
    return values


def _format_single_set(operator: str, derived_set: Any) -> str:
    is_negation = operator == "not_in_set"
    key = derived_set.key
    singular_label = column_label(key)
    singular = singular_label.casefold()
    plural = PLURAL_SUBJECTS.get(singular, f"{singular}s")
    inner_plan = derived_set.plan

    currency_suffix = ""
    for leaf in iter_filter_leaves(inner_plan.filters):
        if (
            not isinstance(leaf, AttributePredicate)
            and leaf.member.casefold().endswith(("currency", "currency_code"))
            and leaf.operator == "eq"
            and leaf.values
        ):
            currency_suffix = f" ({leaf.values[0]})"
            break

    if derived_set.mode == "ranked":
        limit = inner_plan.limit
        limit_str = f"the top {limit} " if limit is not None else "the top "
        measure_label = column_label(inner_plan.measures[0]) if inner_plan.measures else ""
        if is_negation:
            return f"Excludes {limit_str}{plural} by {measure_label}{currency_suffix}"
        return f"Limited to {limit_str}{plural} by {measure_label}{currency_suffix}"

    if inner_plan.measures:
        measure_label = column_label(inner_plan.measures[0])
        if is_negation:
            return f"Excludes {plural} with {measure_label}{currency_suffix}"
        return f"Limited to {plural} with {measure_label}{currency_suffix}"

    inner_subject = _subject(inner_plan)
    inner_facts = _facts(inner_plan)
    condition = f"{', '.join(inner_facts)} {inner_subject}" if inner_facts else inner_subject
    if is_negation:
        return f"Excludes {plural} with {condition}"
    return f"Limited to {plural} with {condition}"


def _fallback_membership_contexts(
    plan: BusinessQueryPlan, derived_map: dict[str, Any]
) -> list[str]:
    """Safety net for ``format_set_context``: derive each referenced set's REAL
    operator directly from every filter leaf, rather than assuming one.

    ``query_plan._validate_derived_sets`` guarantees every declared derived set is
    referenced by a real in_set/not_in_set leaf somewhere in ``plan.filters``,
    and the boolean-aware walk in ``format_set_context`` discovers every such
    leaf regardless of ``all``/``any`` nesting, so this path is unreachable for
    a valid plan today. It exists as defense in depth against a future change
    to that walk reopening the gap: guessing a polarity here risks describing
    the opposite of what the SQL did (rendering a not_in_set exclusion as an
    inclusion), so a set with no discoverable operator is omitted (fail
    closed) rather than rendered with an invented one.
    """
    referenced: dict[str, str] = {}
    for leaf in iter_filter_leaves(plan.filters):
        operator = getattr(leaf, "operator", None)
        if operator in ("in_set", "not_in_set") and leaf.values:
            set_id = str(leaf.values[0])
            if set_id in derived_map and set_id not in referenced:
                referenced[set_id] = operator
    return [
        _format_single_set(operator, derived_map[set_id]) for set_id, operator in referenced.items()
    ]


def format_set_context(plan: BusinessQueryPlan) -> str:
    """Format human-readable membership context for derived sets in a plan."""
    if not plan.derived_sets:
        return ""

    derived_map = {d.id: d for d in plan.derived_sets}

    def _format_branch(node: Any) -> str | None:
        if node is None:
            return None
        if isinstance(node, AttributePredicate):
            val_str = ", ".join(str(v) for v in node.values) if node.values else ""
            return f"{node.family_key}: {val_str}" if val_str else node.family_key
        if hasattr(node, "operator"):
            if node.operator in ("in_set", "not_in_set"):
                set_id = str(node.values[0]) if node.values else ""
                derived = derived_map.get(set_id)
                if derived:
                    return _format_single_set(node.operator, derived)
                return None
            return _format_filter(node)
        # A FilterGroup may have BOTH branches populated at once (`all` AND
        # `any` on the same node) -- render both rather than early-returning
        # on whichever branch is checked first, or a membership sentence
        # living in the unchecked branch silently vanishes.
        all_text: str | None = None
        if hasattr(node, "all") and node.all:
            parts = [p for c in node.all if (p := _format_branch(c))]
            if parts:
                all_text = (
                    parts[0]
                    if len(parts) == 1
                    else " AND ".join(
                        f"({p})" if " " in p and not (p.startswith("(") and p.endswith(")")) else p
                        for p in parts
                    )
                )
        any_text: str | None = None
        if hasattr(node, "any") and node.any:
            parts = [p for c in node.any if (p := _format_branch(c))]
            if parts:
                any_text = f"Matching any of: {' OR '.join(f'({p})' for p in parts)}"
        if all_text and any_text:
            return f"{all_text} AND {any_text}"
        return all_text or any_text

    def _node_has_set(node: Any) -> bool:
        if node is None:
            return False
        if hasattr(node, "operator") and node.operator in ("in_set", "not_in_set"):
            return True
        found = False
        if hasattr(node, "all") and node.all:
            found = found or any(_node_has_set(c) for c in node.all)
        if hasattr(node, "any") and node.any:
            found = found or any(_node_has_set(c) for c in node.any)
        return found

    def _walk_conjunction(node: Any) -> list[str]:
        if node is None:
            return []
        if hasattr(node, "operator") and node.operator in ("in_set", "not_in_set"):
            set_id = str(node.values[0]) if node.values else ""
            derived = derived_map.get(set_id)
            if derived:
                return [_format_single_set(node.operator, derived)]
            return []
        if hasattr(node, "any") and node.any:
            if _node_has_set(node):
                text = _format_branch(node)
                return [text] if text else []
            return []
        if hasattr(node, "all") and node.all:
            results: list[str] = []
            for child in node.all:
                results.extend(_walk_conjunction(child))
            return results
        return []

    contexts: list[str] = []
    if plan.filters is not None:
        contexts = _walk_conjunction(plan.filters)

    if not contexts:
        contexts = _fallback_membership_contexts(plan, derived_map)

    return "; ".join(contexts)


def should_use_deterministic_presentation(plan: BusinessQueryPlan, answered: Answered) -> bool:
    """Default simple single-record details, scalar counts, and small entity rows
    to fast deterministic presentation.
    """
    if bool(answered.record_details):
        return True
    if plan.grain == "scalar":
        return True
    if plan.grain == "entity_rows" and len(answered.rows) <= 1:
        return True
    return False


def present_answered(plan: BusinessQueryPlan, answered: Answered) -> str:
    # Adapters must already bound rows; retain that invariant at this pure boundary.
    rows = answered.rows[: plan.limit]
    if not rows and not answered.record_details:
        base = NO_MATCHING_ROWS
    elif not rows and answered.record_details:
        base = _present_record_details(answered.record_details)
    elif plan.grain == "scalar":
        base = _present_scalar(plan.measures[0], rows[0], column_kinds(answered))
    elif plan.grain == "grouped":
        base = _present_grouped(plan, rows, answered.total_row_count, column_kinds(answered))
    else:
        base = _present_entity_rows(rows, answered.total_row_count)

    if answered.record_details and base != NO_MATCHING_ROWS:
        base = _present_record_details(answered.record_details, base_text=base)

    context = format_set_context(plan)
    if context:
        return f"{context}\n\n{base}"
    return base


def _present_record_details(record_details: list[RecordDetail], base_text: str = "") -> str:
    lines = [base_text] if base_text and base_text != NO_MATCHING_ROWS else []
    for detail in record_details:
        val = detail.display_value if detail.display_value is not None else detail.typed_value
        if val is not None:
            lines.append(f"{detail.family}: {val}")
        else:
            lines.append(f"{detail.family}: {MISSING}")
    return "\n".join(lines)


def _present_scalar(measure: str, row: dict, kinds: dict[str, str]) -> str:
    if measure in row:
        value = row[measure]
    elif len(row) == 1:
        value = next(iter(row.values()))  # positional fallback for a single-value row
    else:
        value = MISSING
    formatted_val = format_cell_value(value, kinds.get(measure)) if value != MISSING else MISSING
    return f"{measure}: {formatted_val}"


def _present_grouped(
    plan: BusinessQueryPlan, rows: list[dict], total_row_count: int, kinds: dict[str, str]
) -> str:
    # Group label = dimensions + bucket_set (a bucket-set-only plan is legal --
    # the bucket label IS the group label, never an empty prefix).
    label_members = [*plan.dimensions, *([plan.bucket_set] if plan.bucket_set else [])]
    lines = []
    for row in rows:
        labels = group_row_label(row, label_members)
        values = " / ".join(
            format_cell_value(row.get(member, MISSING), kinds.get(member))
            for member in plan.measures
        )
        lines.append(f"{labels}: {values}")
    if total_row_count == len(rows):
        lines.append(f"({len(rows)} groups)")
    else:
        lines.append(f"({len(rows)} of {total_row_count} groups)")
    return "\n".join(lines)


def _present_entity_rows(rows: list[dict], total_row_count: int) -> str:
    # Row values, not keyed lookups -- entity_rows has no defensive-key rule.
    lines = [" | ".join(str(value) for value in row.values()) for row in rows]
    lines.append(f"Showing {len(rows)} of {total_row_count}")
    return "\n".join(lines)


def render_rich(
    question: str,
    plan: BusinessQueryPlan,
    answered: Answered,
    *,
    model_factory: Callable[[], Any],
    fallback: Callable[[BusinessQueryPlan, Answered], str] | None = None,
    call_recorder: CallRecorder | None = None,
    trace: Any = None,
) -> str:
    """Optional one-call prose renderer. Counted as a model call.

    The model sees ONLY the question, the plan's member names, and the typed
    rows (bounded to ``plan.limit``) -- never the receipt, hashes,
    fingerprints, or SQL. On any exception, presentation must never turn an
    ``Answered`` into a failure outcome: log and fall back to ``fallback``
    when the caller supplies a plan-aware presenter, else to
    ``present_answered``.
    """
    import time

    start = time.perf_counter()
    if call_recorder is not None:
        call_recorder("renderer")
    try:
        model = model_factory()
        bound = model.with_structured_output(_RENDER_REPLY_SCHEMA, method="json_schema")
        payload = {
            "question": question,
            "members": sorted(plan_member_names(plan)),
            "rows": answered.rows[: plan.limit],
        }
        # A bare dict is rejected by the chat model's input conversion (only
        # str/messages/PromptValue are accepted). default=str is mandatory,
        # not cosmetic -- adapter rows carry Decimal/date values that bare
        # json.dumps refuses. HumanMessage-only: no system instruction, so
        # the model sees ONLY question/members/rows.
        content = json.dumps(payload, default=str, sort_keys=True)
        raw = bound.invoke([HumanMessage(content=content)])
        answer = raw["answer"]
        if not isinstance(answer, str) or not answer.strip() or len(answer) > _MAX_RENDER_CHARS:
            raise ValueError("invalid rich renderer reply")
        return answer
    except Exception as exc:  # noqa: BLE001 — presentation must never fail an Answered
        logger.warning("render_rich failed: %s", type(exc).__name__)
        return (fallback or present_answered)(plan, answered)
    finally:
        if trace is not None:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if hasattr(trace, "record_rich_render"):
                trace.record_rich_render(elapsed_ms)
            else:
                setattr(trace, "rich_render_ms", elapsed_ms)


def format_currency(
    amount: float | int | Decimal | str,
    *,
    currency: str = "KES",
    compact: bool = False,
) -> str:
    """Format numeric amount with currency prefix and thousands commas or compact magnitude."""
    try:
        val = float(amount)
    except (ValueError, TypeError):
        return f"{currency} {amount}"

    if compact:
        if abs(val) >= 1_000_000_000:
            formatted = f"{val / 1_000_000_000:.1f}".rstrip("0").rstrip(".")
            return f"{currency} {formatted}B"
        if abs(val) >= 1_000_000:
            formatted = f"{val / 1_000_000:.1f}".rstrip("0").rstrip(".")
            return f"{currency} {formatted}M"
        if abs(val) >= 1_000:
            formatted = f"{val / 1_000:.1f}".rstrip("0").rstrip(".")
            return f"{currency} {formatted}K"

    return f"{currency} {val:,.2f}"


def format_subject_restatement(
    count: int,
    subject: str,
    *,
    total_amount: float | int | Decimal | None = None,
    currency: str = "KES",
) -> str:
    """Restate subject and format aggregate totals for single-fact / aggregate responses."""
    if total_amount is not None:
        formatted_amt = format_currency(total_amount, currency=currency)
        return f"Found **{count} {subject}**, totalling **{formatted_amt}**."
    return f"Found **{count} {subject}**."


def format_markdown_table(
    rows: list[dict[str, Any]],
    *,
    column_labels: dict[str, str] | None = None,
    headers: list[str] | None = None,
    currency: str = "KES",
) -> str:
    """Format row dictionaries as standard GFM markdown table with currency formatting."""
    if not rows:
        return ""

    keys = headers if headers is not None else list(rows[0].keys())
    labels_map = column_labels or {}
    display_headers = [labels_map.get(k, k.replace("_", " ").title()) for k in keys]

    header_line = "| " + " | ".join(display_headers) + " |"
    separator_line = "|" + "|".join(["---"] * len(keys)) + "|"

    data_lines = []
    for row in rows:
        formatted_cells = []
        for k in keys:
            val = row.get(k, "")
            k_lower = k.lower()
            if any(
                term in k_lower
                for term in ("amount", "total", "subtotal", "price", "balance", "cost")
            ) and isinstance(val, (int, float, Decimal)):
                formatted_cells.append(format_currency(val, currency=currency))
            elif val is None:
                formatted_cells.append(MISSING)
            else:
                formatted_cells.append(str(val))
        data_lines.append("| " + " | ".join(formatted_cells) + " |")

    return "\n".join([header_line, separator_line, *data_lines])
