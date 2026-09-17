"""Extract id-only lineage and turn fields from captured producer results."""

from __future__ import annotations

from typing import Any

_PRODUCER_OPS = frozenset(
    {
        "coordinator_decision",
        "answer_generation",
        "evidence_restore",
        "planner",
        "business_sql",
        "document_retrieval",
        "record_rehydration",
        "condense",
    }
)


def _as_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return []


def _exchange_id(obj: Any) -> str | None:
    if obj is None:
        return None
    eid = getattr(obj, "exchange_id", None)
    if eid:
        return str(eid)
    bindings = getattr(obj, "bindings", None)
    bound = getattr(bindings, "exchange_id", None) if bindings is not None else None
    if bound:
        return str(bound)
    if isinstance(obj, dict) and obj.get("exchange_id"):
        return str(obj["exchange_id"])
    return None


def extra_from_call(
    operation: str, result: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    sources: list[str] = []
    persisted: list[str] = []
    _collect_lineage(result, sources, persisted, depth=0)
    for candidate in (*args, *kwargs.values()):
        _collect_lineage(candidate, sources, persisted, depth=0)
    target = kwargs.get("target_exchange_id")
    if target:
        persisted.append(str(target))
        extra["replacement"] = True
    if sources:
        extra["source_exchange_ids"] = _unique(sources)
    if persisted:
        extra["persisted_exchange_ids"] = _unique(persisted)
    extra.update(_turn_fields(operation, result))
    lifecycle = kwargs.get("lifecycle")
    if lifecycle is None and args:
        for arg in args:
            if hasattr(arg, "elapsed_ms"):
                lifecycle = arg
                break
    action_id = kwargs.get("action_id") or kwargs.get("step_id")
    if action_id is None and args:
        for arg in args:
            if isinstance(arg, str) and (arg.startswith("act-") or arg.startswith("action-")):
                action_id = arg
                break
    extra.update(extract_search_fields(result, lifecycle=lifecycle, action_id=action_id))
    return extra


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    for item in values:
        if item not in out:
            out.append(item)
    return out


_MAX_WALK = 4
_OUTCOME_TYPES = frozenset(
    {"FinishedDraft", "ClarifyRequested", "BusinessQueryTerminal", "Stopped"}
)


def _collect_lineage(obj: Any, sources: list[str], persisted: list[str], *, depth: int) -> None:
    if obj is None or depth > _MAX_WALK:
        return
    if isinstance(obj, (str, bytes, int, float, bool)):
        return
    if isinstance(obj, dict):
        sources.extend(_as_ids(obj.get("source_exchange_ids")))
        if "restore_refs" in obj:
            sources.extend(_as_ids(obj.get("exchange_ids")))
        persisted_id = _exchange_id(obj)
        if persisted_id:
            persisted.append(persisted_id)
        return
    if isinstance(obj, (list, tuple, set)):
        for item in obj:
            _collect_lineage(item, sources, persisted, depth=depth + 1)
        return
    sources.extend(_as_ids(getattr(obj, "source_exchange_ids", None)))
    name = type(obj).__name__
    if name == "SelectedSources":
        sources.extend(_as_ids(getattr(obj, "exchange_ids", None)))
    persisted_id = _exchange_id(obj)
    if persisted_id:
        persisted.append(persisted_id)
    if name in _OUTCOME_TYPES:
        _collect_lineage(getattr(obj, "outcomes", None), sources, persisted, depth=depth + 1)
    if name == "ActionOutcome":
        _collect_lineage(getattr(obj, "result", None), sources, persisted, depth=depth + 1)
    if name == "RestoreResponse":
        _collect_lineage(getattr(obj, "results", None), sources, persisted, depth=depth + 1)
    if name == "RestoredEvidence":
        _collect_lineage(getattr(obj, "payload", None), sources, persisted, depth=depth + 1)


def _turn_fields(operation: str, result: Any) -> dict[str, Any]:
    if operation != "coordinator_decision" or result is None:
        return {}
    extra: dict[str, Any] = {}
    if isinstance(result, dict):
        if "outcome_type" in result:
            extra["outcome_type"] = result.get("outcome_type")
        if "answer_mode" in result:
            extra["answer_mode"] = result.get("answer_mode")
        if "decisions" in result and result.get("decisions") is not None:
            extra["decisions"] = result.get("decisions")

        last_action = result.get("last_action")
        if last_action is not None:
            kind = getattr(last_action, "kind", None)
            if kind is None and isinstance(last_action, dict):
                kind = last_action.get("kind")
            if kind:
                extra["action_kind"] = kind
        elif "action_kind" in result:
            extra["action_kind"] = result["action_kind"]
        elif "kind" in result and not isinstance(result["kind"], dict):
            extra["action_kind"] = result["kind"]

        terminal = result.get("terminal")
        if terminal is not None:
            t_name = type(terminal).__name__
            if t_name == "ClarifyRequested":
                extra.setdefault("outcome_type", "clarification_required")
            elif t_name == "FinishedDraft":
                extra.setdefault("outcome_type", "answered")
                if hasattr(terminal, "answer_mode"):
                    extra.setdefault("answer_mode", getattr(terminal, "answer_mode"))
            elif t_name == "Stopped":
                extra.setdefault("outcome_type", "stopped")
            elif t_name == "BusinessQueryTerminal":
                extra.setdefault("outcome_type", "answered")

            t_ot = getattr(terminal, "outcome_type", None)
            if t_ot and "outcome_type" not in extra:
                extra["outcome_type"] = t_ot

            t_reason = getattr(terminal, "reason", None)
            if isinstance(terminal, dict):
                t_reason = terminal.get("reason", t_reason)
            if t_reason == "malformed_response":
                mk = (
                    getattr(terminal, "malformed_kind", None)
                    or getattr(result, "malformed_kind", None)
                    or (terminal.get("malformed_kind") if isinstance(terminal, dict) else None)
                    or result.get("malformed_kind")
                )
                if mk is not None:
                    extra["malformed_kind"] = mk
        return extra

    if hasattr(result, "kind") and type(result).__name__ != "ActionOutcome":
        extra["action_kind"] = getattr(result, "kind")

    name = type(result).__name__
    if name == "ClarifyRequested":
        extra["outcome_type"] = "clarification_required"
    elif name == "FinishedDraft":
        extra["outcome_type"] = "answered"
        extra["answer_mode"] = getattr(result, "answer_mode", None)
    elif name == "Stopped":
        extra["outcome_type"] = "stopped"
        if getattr(result, "reason", None) == "malformed_response":
            mk = getattr(result, "malformed_kind", None)
            if mk is not None:
                extra["malformed_kind"] = mk
    elif name == "BusinessQueryTerminal":
        extra["outcome_type"] = "answered"

    if getattr(result, "reason", None) == "malformed_response":
        extra.setdefault("outcome_type", "stopped")
        mk = getattr(result, "malformed_kind", None)
        if mk is not None and "malformed_kind" not in extra:
            extra["malformed_kind"] = mk

    ot = getattr(result, "outcome_type", None)
    if ot and "outcome_type" not in extra:
        extra["outcome_type"] = ot
    if "answer_mode" not in extra and hasattr(result, "answer_mode") and name == "FinishedDraft":
        extra["answer_mode"] = getattr(result, "answer_mode", None)
    return extra


def extract_search_fields(
    result: Any,
    *,
    lifecycle: Any = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    """Extract search telemetry extras: passage_count, passage_ids, truncated, elapsed_ms."""
    if result is None:
        return {}

    outcome_elapsed: int | None = None
    search_result = result
    if getattr(result, "kind", None) == "search_documents":
        outcome_elapsed = getattr(result, "elapsed_ms", None)
        search_result = getattr(result, "result", None)
        if action_id is None:
            action_id = getattr(result, "action_id", None)
    elif type(result).__name__ == "ActionOutcome":
        search_result = getattr(result, "result", None)
        outcome_elapsed = getattr(result, "elapsed_ms", None)
        if action_id is None:
            action_id = getattr(result, "action_id", None)

    passages: Any = None
    truncated: bool = False
    if search_result is not None:
        if hasattr(search_result, "passages"):
            passages = getattr(search_result, "passages", ())
            truncated = bool(getattr(search_result, "truncated", False))
        elif isinstance(search_result, dict) and "passages" in search_result:
            passages = search_result.get("passages", ())
            truncated = bool(search_result.get("truncated", False))

    if passages is None:
        if getattr(result, "kind", None) == "search_documents":
            elapsed: int | None = outcome_elapsed
            if (
                elapsed is None
                and lifecycle is not None
                and action_id
                and hasattr(lifecycle, "elapsed_ms")
            ):
                elapsed = lifecycle.elapsed_ms(action_id)
            extra: dict[str, Any] = {}
            if elapsed is not None:
                extra["elapsed_ms"] = int(elapsed)
            return extra
        return {}

    passage_ids: list[str] = []
    for p in passages:
        pid = getattr(p, "id", None)
        if pid is None and isinstance(p, dict):
            pid = p.get("id")
        if pid is not None:
            passage_ids.append(str(pid))
        elif isinstance(p, str):
            passage_ids.append(p)

    elapsed = outcome_elapsed
    if elapsed is None and search_result is not None:
        elapsed = getattr(search_result, "elapsed_ms", None)
        if elapsed is None and isinstance(search_result, dict):
            elapsed = search_result.get("elapsed_ms")
    if elapsed is None and lifecycle is not None and action_id and hasattr(lifecycle, "elapsed_ms"):
        elapsed = lifecycle.elapsed_ms(action_id)

    extra = {
        "passage_count": len(passage_ids),
        "passage_ids": passage_ids,
        "truncated": truncated,
    }
    if elapsed is not None:
        extra["elapsed_ms"] = int(elapsed)
    return extra


search_capture_fields = extract_search_fields


def lineage_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    sources: list[Any] = []
    persisted: list[Any] = []
    outcome_types: list[Any] = []
    answer_modes: list[Any] = []
    replacements = 0
    for event in events:
        for item in event.get("source_exchange_ids") or []:
            if item not in sources:
                sources.append(item)
        for item in event.get("persisted_exchange_ids") or []:
            if item not in persisted:
                persisted.append(item)
        if event.get("outcome_type") is not None:
            outcome_types.append(event["outcome_type"])
        if "answer_mode" in event:
            answer_modes.append(event.get("answer_mode"))
        if event.get("replacement") is True:
            replacements += 1
    return {
        "source_exchange_ids": sources,
        "persisted_exchange_ids": persisted,
        "outcome_types": outcome_types,
        "outcome_type": outcome_types[-1] if outcome_types else None,
        "answer_modes": answer_modes if answer_modes else None,
        "replacement_exchanges": replacements,
        "work_step_counts": _work_step_counts(events),
        "new_call_after_cancel": _new_call_after_cancel(events),
    }


def _work_step_counts(events: list[dict[str, Any]]) -> list[int]:
    counts: list[int] = []
    current = 0
    in_turn = False
    for event in events:
        if event.get("operation") == "coordinator_decision" and event.get("phase") == "begin":
            if in_turn:
                counts.append(current)
            current = 0
            in_turn = True
        if event.get("operation") == "business_sql" and event.get("phase") == "attempt":
            current += 1
    if in_turn:
        counts.append(current)
    return counts


def _new_call_after_cancel(events: list[dict[str, Any]]) -> bool:
    cancelled = False
    for event in events:
        if event.get("phase") == "cancelled":
            cancelled = True
            continue
        if cancelled and event.get("phase") in {"begin", "attempt"}:
            if event.get("operation") in _PRODUCER_OPS:
                return True
    return False
