"""Deterministic follow-up suggestions outside the LLM completion path.

Suggestions are derived from the SIGNED MANIFEST -- the same artifact the
executor authorizes against -- rather than invented. A chip is offered only
when:

  1. the question names a resource the manifest actually declares, and
  2. the manifest declares the operation that chip would trigger, and
  3. the running configuration would not refuse that operation
     (``record_analytics_mode``).

This keeps a strict safety property: never imply a capability the request
did not prove. No model is called and nothing here runs on the answer
critical path: the caller (``app/services/answer_finalize.py``) invokes this
after the answer is finalized.

Scope limit, deliberate and documented: this function receives only
(question, answer, history) and therefore has NO principal. It reasons about
what the MANIFEST declares, never about what the asking principal is GRANTED.
A suggested chip may still be denied by ``PolicyScopedRecordExecutor`` when
clicked -- the executor remains the sole authorization authority, exactly as
for any other request. Suggestions are bounded to resources the user's own
question already named, so a chip cannot disclose the existence of a resource
they did not mention.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from app.auth import Principal
from app.config import settings
from app.conversation.transcript_models import TranscriptTurn
from app.policy.manifest_loader import Manifest, load_manifest

# Retained for records-only prompt history until that primary answer path is
# independently revised. This suggestion module never sends history to a
# model.
HISTORY_WINDOW = 6

SuggestionMode = Literal["none", "page_context", "record_domain"]

# Aggregate chips are withheld unless analytics are actually switched on.
# ``record_analytics_mode`` ships as "disabled", and offering "How many jobs
# are there?" against a configuration that answers operation_unsupported is
# the precise failure this module is supposed to prevent.
_ANALYTICS_OFF = "disabled"

_MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class FollowUpSuggestionDecision:
    """Wire-safe deterministic follow-up outcome."""

    mode: SuggestionMode
    suggestions: list[str]


_NO_SUGGESTIONS = FollowUpSuggestionDecision(mode="none", suggestions=[])


def normalize_follow_up_suggestions(suggestions: list[str]) -> list[str]:
    """Normalize, deduplicate, and bound a prevalidated deterministic list."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in suggestions:
        suggestion = " ".join(str(raw).split())[:160].strip()
        key = suggestion.casefold()
        if len(suggestion) < 2 or key in seen:
            continue
        seen.add(key)
        normalized.append(suggestion)
        if len(normalized) == _MAX_SUGGESTIONS:
            break
    return normalized


def _plural(label: str) -> str:
    """Enough English for the manifest's own resource names ("inventory" ->
    "inventories", "credit note" -> "credit notes"). Not a general
    pluralizer -- the vocabulary is closed and comes from the manifest."""
    if label.endswith("y") and not label.endswith(("ay", "ey", "iy", "oy", "uy")):
        return label[:-1] + "ies"
    if label.endswith(("s", "x", "z", "ch", "sh")):
        return label + "es"
    return label + "s"


@lru_cache(maxsize=1)
def _resource_patterns(resource_types: tuple[str, ...]) -> tuple[tuple[str, str, re.Pattern], ...]:
    """One word-boundary pattern per declared resource, longest label first.

    Longest-first matters: "customer order" and "customer" are both declared,
    and the shorter one is a substring of the longer. Word boundaries stop
    "job" matching inside "jobless" -- the same pattern the route guard
    already uses.
    """
    entries = []
    for resource_type in resource_types:
        label = resource_type.replace("_", " ")
        pattern = re.compile(
            rf"\b(?:{re.escape(label)}|{re.escape(_plural(label))})\b",
            re.IGNORECASE,
        )
        entries.append((resource_type, label, pattern))
    entries.sort(key=lambda e: len(e[1]), reverse=True)
    return tuple(entries)


def reachable_resources(manifest: Manifest, principal: Principal | None) -> tuple[str, ...]:
    """Manifest resources this caller may actually read.

    The manifest declares what the deployment has; the v2 record_access
    snapshot declares what this principal may reach, and it is the only
    authorization source RAG honours (see ManifestResource's docstring).
    A v1 token carries no snapshot, so nothing here is provably reachable
    and no chip is offered -- an offer we cannot stand behind is worse than
    no offer.
    """
    declared = set(manifest.resources)
    granted = getattr(principal, "resources", None) if principal is not None else None
    if granted is None:
        return ()
    return tuple(sorted(declared & set(granted)))


def _detect_resource(
    question: str, manifest: Manifest, principal: Principal | None = None
) -> str | None:
    resources = (
        reachable_resources(manifest, principal)
        if principal is not None
        else tuple(sorted(manifest.resources))
    )
    for resource_type, _label, pattern in _resource_patterns(resources):
        if pattern.search(question):
            return resource_type
    return None


def _manifest_grounded_suggestions(resource_type: str, manifest: Manifest) -> list[str]:
    resource = manifest.resources.get(resource_type)
    if resource is None:  # pragma: no cover - _detect_resource only yields declared names
        return []

    operations = set(resource.operations)
    analytics_on = settings.record_analytics_mode != _ANALYTICS_OFF
    plural = _plural(resource_type.replace("_", " "))

    suggestions: list[str] = []
    if analytics_on and "count" in operations:
        suggestions.append(f"How many {plural} are there?")
    if analytics_on and "group_count" in operations and resource.groupable_fields:
        field = resource.groupable_fields[0].replace("_", " ")
        suggestions.append(f"Show {plural} grouped by {field}")
    if "list" in operations:
        suggestions.append(f"Show the most recent {plural}")
    return suggestions


def select_follow_up_suggestions(
    question: str,
    _answer: str,
    _history: list[TranscriptTurn] | None = None,
    *,
    principal: Principal | None = None,
) -> FollowUpSuggestionDecision:
    """Deterministic, manifest-grounded follow-up chips for a record question.

    Returns the explicit no-suggestion mode whenever the question names no
    declared resource -- the safe fallback is preserved for every document,
    general-help, and unrecognized turn.
    """
    try:
        manifest = load_manifest()
    except Exception:
        # A manifest that will not load is the executor's problem to report;
        # suggestions must never be the thing that fails a turn.
        return _NO_SUGGESTIONS

    resource_type = _detect_resource(question, manifest, principal=principal)
    if resource_type is None:
        return _NO_SUGGESTIONS

    suggestions = normalize_follow_up_suggestions(
        _manifest_grounded_suggestions(resource_type, manifest)
    )
    if not suggestions:
        return _NO_SUGGESTIONS
    return FollowUpSuggestionDecision(mode="record_domain", suggestions=suggestions)


async def generate_follow_up_suggestions(
    question: str,
    answer: str,
    history: list[TranscriptTurn] | None = None,
    *,
    principal: Principal | None = None,
) -> list[str]:
    """Compatibility seam with no model or external call."""
    return select_follow_up_suggestions(question, answer, history, principal=principal).suggestions


def generate_follow_up_suggestions_from_bq(
    *,
    question: str,
    answer: str,
    record_links: Sequence[Any] | None = None,
    history: list[TranscriptTurn] | None = None,
    principal: Principal | None = None,
) -> list[str]:
    """Generate 2 to 3 entity-grounded follow-up suggestions strictly from resolved BQ entities."""
    suggestions: list[str] = []
    if record_links:
        for link in record_links:
            label = getattr(link, "label", "") or ""
            table = getattr(link, "table", "") or ""
            if not label:
                continue
            safe_label = label[:60] if len(label) > 60 else label
            if table == "customers" or getattr(link, "resource_type", "") == "customer":
                suggestions.append(f"Show all unpaid invoices for {safe_label}")
                suggestions.append(f"What jobs are in production for {safe_label}?")
            elif table == "work_orders" or getattr(link, "resource_type", "") == "job":
                suggestions.append(f"What materials are allocated to {safe_label}?")
                suggestions.append(f"Show delivery status for {safe_label}")
            elif table == "bills" or getattr(link, "resource_type", "") == "invoice":
                suggestions.append(f"Show all jobs associated with {safe_label}")
                suggestions.append(f"What is the payment status for {safe_label}?")
            if len(suggestions) >= 3:
                break

    if not suggestions:
        decision = select_follow_up_suggestions(question, answer, history, principal=principal)
        suggestions = decision.suggestions

    return normalize_follow_up_suggestions(suggestions)
