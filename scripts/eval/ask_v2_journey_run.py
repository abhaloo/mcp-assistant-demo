"""Drive the panel's v2 wire — the SSE stream and the reply door — as an oracle.

Each journey case POSTs an ``AskV2Request`` with ``Accept: text/event-stream``
and reads the frames the way the browser does: ``turn_accepted``, ``text_delta``,
``table_*``, ``interaction``, ``turn_outcome``, ``stream_error``. A case whose
primary turn ends in ``interaction`` and names ``reply`` takes the reply door,
and the same expectations hold for the reply's observation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from scripts.eval.eval_tokens import mint_eval_token

DEFAULT_DEADLINE_MS = 240_000
_TERMINAL_FRAMES = {"turn_outcome", "stream_error"}


@dataclass
class TurnObservation:
    """One turn as the wire showed it, ready for expectation comparison."""

    status: int = 0
    elapsed_s: float = 0.0
    keep_alives: int = 0
    frames: list[dict[str, Any]] = field(default_factory=list)
    # Derived lookups the expectation keys name.
    outcome: dict[str, Any] | None = None
    interaction: dict[str, Any] | None = None
    answer_text: str = ""
    tables: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    restore_ref: str | None = None
    budget: dict[str, Any] | None = None


@dataclass
class JourneyCase:
    """One journey case, its primary observation, and the bodies it posted."""

    case: dict[str, Any]
    observation: TurnObservation
    turns: list[dict[str, Any]] = field(default_factory=list)
    reply: TurnObservation | None = None

    @property
    def id(self) -> Any:
        return self.case.get("id")


def parse_sse(body: str) -> TurnObservation:
    """Turn one SSE body into a TurnObservation.

    ``event:`` / ``data:`` pairs become frames; ``: keep-alive`` comments are
    counted but never parsed; the read walks the stream until the first
    ``turn_outcome`` or ``stream_error``.
    """
    observation = TurnObservation()
    for block in body.split("\n\n"):
        stripped = block.strip()
        if not stripped:
            continue
        if stripped.startswith(":"):
            observation.keep_alives += 1
            continue
        payload = _decode_block(block)
        if payload is None:
            continue
        observation.frames.append(payload)
        _fold_frame(observation, payload)
        if observation.outcome is not None:
            break
    return observation


def _decode_block(block: str) -> dict[str, Any] | None:
    event_name: str | None = None
    data_lines: list[str] = []
    for line in block.split("\n"):
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines and event_name is None:
        return None
    try:
        payload = json.loads("\n".join(data_lines)) if data_lines else {}
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("event_type") is None:
        payload["event_type"] = event_name
    return payload


def _sse_from_lines(lines: Any) -> list[dict[str, Any]]:
    """The frames of one streamed response, stopping at the terminal kind."""
    frames: list[dict[str, Any]] = []
    for line in lines:
        if not line or line.startswith(":") or line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        frames.append(payload)
        if payload.get("event_type") in _TERMINAL_FRAMES:
            break
    return frames


def _fold(observation: TurnObservation) -> None:
    """Populate the comparison-facing fields from the frames in hand."""
    for frame in observation.frames:
        _fold_frame(observation, frame)


def _fold_frame(observation: TurnObservation, frame: dict[str, Any]) -> None:
    """Fold one frame's contribution into the observation fields."""
    kind = frame.get("event_type")
    if kind in _TERMINAL_FRAMES:
        observation.outcome = frame
    elif kind == "interaction":
        observation.interaction = frame
    elif kind == "text_delta":
        if frame.get("content_kind", "narrative") != "table_fallback":
            observation.answer_text += str(frame.get("delta", ""))
    elif kind == "table_start":
        observation.tables += 1
    elif kind == "table_rows":
        observation.rows.extend(frame.get("rows") or [])
    elif kind == "turn_accepted":
        observation.restore_ref = frame.get("restore_ref")
    if frame.get("budget") is not None:
        observation.budget = frame["budget"]


def _simplify(observation: TurnObservation) -> dict[str, Any]:
    """The comparison-facing record of one stream."""
    return {
        "status": observation.status,
        "elapsed_s": observation.elapsed_s,
        "outcome": observation.outcome,
        "interaction": observation.interaction,
        "restore_ref": observation.restore_ref,
        "answer_text": observation.answer_text,
        "tables": observation.tables,
        "rows": observation.rows,
        "budget": observation.budget,
    }


_ALLOWED_BUDGET_FIELDS = {"used_usd", "this_turn_usd", "reset_at", "period_end", "limit_usd"}


def compare_turn(expected: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    """Frame-level expectation keys for one observation.

    Any other key is a journey-authoring error: explicit failure, never silence.
    """
    mismatches: list[str] = []
    for key, want in expected.items():
        mismatched = _check(key, want, observed)
        if mismatched:
            mismatches.append(mismatched)
    return mismatches


def _check(key: str, want: Any, observed: dict[str, Any]) -> str | None:
    handler: Callable[..., bool] | None
    lookup = key
    if key.startswith("budget."):
        field_name = key.removeprefix("budget.")
        lookup = field_name.removesuffix("_present")
        handler = _budget_handler(field_name)
    else:
        handler = _FIELD_CHECKS.get(key)
    if handler is None:
        raise ValueError(f"unknown expectation: {key}")
    if handler(want, observed, lookup):
        return None
    return "interaction" if key == "no_interaction" else ("tables" if key == "tables_min" else key)


def _field_equal(want: Any, observed: dict[str, Any], key: str) -> bool:
    return (observed.get("outcome") or {}).get(key) == want


def _trusted_is(want: Any, observed: dict[str, Any], _key: str) -> bool:
    return (observed.get("outcome") or {}).get("trusted") is want


def _table_min(want: Any, observed: dict[str, Any], _key: str) -> bool:
    return observed.get("tables", 0) >= want


def _present(want: Any, observed: dict[str, Any], key: str) -> bool:
    return not want or observed.get(key.removesuffix("_present")) is not None


def _no_interaction(want: Any, observed: dict[str, Any], _key: str) -> bool:
    return not want or observed.get("interaction") is None


def _answer_contains(want: Any, observed: dict[str, Any], _key: str) -> bool:
    hay = observed.get("answer_text") or ""
    return any(needle in hay for needle in want)


def _budget_field_equal(want: Any, observed: dict[str, Any], key: str) -> bool:
    return (observed.get("budget") or {}).get(key) == want


def _budget_field_present(want: Any, observed: dict[str, Any], key: str) -> bool:
    return not want or key in (observed.get("budget") or {})


def _budget_handler(field_name: str) -> Callable[..., bool] | None:
    if field_name.endswith("_present") and field_name[:-8] in {
        "used_usd",
        "this_turn_usd",
        "reset_at",
    }:
        return _budget_field_present
    if field_name in {"limit_usd", "period_end"}:
        return _budget_field_equal
    return None


def _terminal_in(want: Any, observed: dict[str, Any], _key: str) -> bool:
    """The terminal kind: outcome_type of a turn_outcome frame, code of a stream_error frame."""
    terminal = observed.get("outcome") or {}
    return (terminal.get("outcome_type") or terminal.get("code")) in want


def _answer_excludes(want: Any, observed: dict[str, Any], _key: str) -> bool:
    hay = observed.get("answer_text") or ""
    return not any(needle in hay for needle in want)


def _summary_contains_all(want: Any, observed: dict[str, Any], _key: str) -> bool:
    wire = (observed.get("outcome") or {}).get("business_query") or {}
    presentation = (wire.get("envelope") or {}).get("presentation") or {}
    summary = presentation.get("summary") or ""
    return bool(summary) and all(needle in summary for needle in want)


def _row_key_prefix_or_notice(want: Any, observed: dict[str, Any], _key: str) -> bool:
    outcome = observed.get("outcome") or {}
    if outcome.get("unanswered_part"):
        return True
    rows = observed.get("rows") or []
    return any(any(str(col).startswith(want) for col in row) for row in rows)


def _row_value_contains_any(want: Any, observed: dict[str, Any], _key: str) -> bool:
    """A painted row carries one of the texts in one of its values."""
    rows = observed.get("rows") or []
    return any(needle in str(value) for row in rows for value in row.values() for needle in want)


_FIELD_CHECKS: dict[str, Callable[..., bool]] = {
    "outcome_type": _field_equal,
    "trusted": _trusted_is,
    "reason_code": _field_equal,
    "tables_min": _table_min,
    "restore_ref_present": _present,
    "no_interaction": _no_interaction,
    "answer_contains_any": _answer_contains,
    "terminal_in": _terminal_in,
    "answer_excludes_all": _answer_excludes,
    "summary_contains_all": _summary_contains_all,
    "row_key_prefix_or_notice": _row_key_prefix_or_notice,
    "row_value_contains_any": _row_value_contains_any,
}


def post_turn(base_url: str, payload: dict[str, Any], headers: dict[str, str]) -> TurnObservation:
    """One POST of the v2 wire, folded into an observation — the single HTTP
    seam a test scripts instead of raising a server."""
    t0 = time.perf_counter()
    observation = TurnObservation()
    timeout = httpx.Timeout(660.0, read=660.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            f"{base_url.rstrip('/')}/api/ask",
            json=payload,
            headers={"Accept": "text/event-stream", **headers},
        )
        observation.status = response.status_code
        observation.frames = _sse_from_lines(response.iter_lines())
    observation.elapsed_s = time.perf_counter() - t0
    _fold(observation)
    return observation


def _turn_payload(
    case: dict[str, Any], thread_id: str, operation: str, **extra: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "protocol_version": "2",
        "operation": operation,
        "run_id": uuid.uuid4().hex,
        "thread_id": thread_id,
        "idempotency_key": uuid.uuid4().hex,
        "deadline_at_ms": int(time.time() * 1000) + DEFAULT_DEADLINE_MS,
        "response_policy": "strict",
    }
    if operation != "clarification_reply" and case.get("question"):
        payload["question"] = case["question"]
    payload.update(extra)
    return payload


def run_journey(path: str | Path, *, base_url: str, profile: str) -> list[JourneyCase]:
    """Run every case of one journey file on one thread; a case whose primary
    turn ends in ``interaction`` and names ``reply`` takes the reply door and
    carries both observations."""
    lines = [
        json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line
    ]
    header: dict[str, Any] = lines[0] if lines and "journey" in lines[0] else {}
    cases: list[dict[str, Any]] = lines[1:] if header else lines

    thread_id = uuid.uuid4().hex
    ask_budget = header.get("ask_budget")

    def fresh_headers() -> dict[str, str]:
        # The verifier consumes each token's jti once, so every request mints its own.
        return {"Authorization": f"Bearer {mint_eval_token(profile, ask_budget=ask_budget)}"}

    results: list[JourneyCase] = []
    for case in cases:
        body = _turn_payload(case, thread_id, case.get("operation", "new_question"))
        observation = post_turn(base_url, body, fresh_headers())
        results.append(JourneyCase(case=case, observation=observation, turns=[body], reply=None))

        interaction = observation.interaction
        if interaction is None or not case.get("reply"):
            thread_id = str(body["thread_id"])
            continue

        option = _reply_option(interaction, str(case["reply"].get("choose", "")))
        if option is None or interaction.get("continuation_ref") is None:
            continue

        reply_body = _turn_payload(
            case,
            str(body["thread_id"]),
            "clarification_reply",
            continuation_ref=interaction["continuation_ref"],
            clarification_choice_id=option["id"],
        )
        reply = post_turn(base_url, reply_body, fresh_headers())
        case_result = results[-1]
        case_result.reply = reply
        case_result.turns.append(reply_body)
        thread_id = str(body["thread_id"])
    return results


def _reply_option(interaction: dict[str, Any], substring: str) -> dict[str, Any] | None:
    return next(
        (o for o in interaction.get("options", []) if substring in str(o.get("label", ""))),
        None,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results = run_journey(args.journey, base_url=args.base_url, profile=args.profile)
    report = [
        {
            "id": case.id,
            "run_id": case.turns[0].get("run_id"),
            "observation": _simplify(case.observation),
            "reply": _simplify(case.reply) if case.reply else None,
            "mismatches": compare_turn(
                case.case.get("expected", {}),
                _simplify(case.reply if case.reply is not None else case.observation),
            ),
        }
        for case in results
    ]
    print(json.dumps(report, indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--journey", required=True)
    parser.add_argument("--profile", default="finance")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
