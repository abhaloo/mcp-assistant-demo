"""Post-exec grain and shape check pure functions.

Reuses frozen cardinality patterns from model_router — do not duplicate or
extend EXPLICIT_MULTIROW_RE / CARDINALITY_INTENT_RE here.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.eval.sql.agent.clarify import CLARIFY_PREFIX
from app.eval.sql.agent.tool_budget import (
    LIMIT_N_RE,
    SQL_RESULT_HEADER_PREFIX,
    SQL_RESULT_TRUNCATION_MARKER,
)
from app.rag.model_router import CARDINALITY_INTENT_RE, EXPLICIT_MULTIROW_RE

Grain = Literal["count_scalar", "singular", "explicit_multirow"]

# Count arm of CARDINALITY_INTENT_RE — split for grain labeling only.
_COUNT_SCALAR_RE = re.compile(
    r"\b(?:how\s+many|number\s+of|count(?:\s+of)?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ShapeOk:
    """Result row count is acceptable for the expected grain."""


@dataclass(frozen=True)
class ShapeMismatch:
    """Result row count does not match expected grain."""

    reason: str


def expected_result_grain(question: str) -> Grain | None:
    """Classify the expected result grain from the original Ask question."""
    q = question or ""
    if EXPLICIT_MULTIROW_RE.search(q):
        return "explicit_multirow"
    if _COUNT_SCALAR_RE.search(q):
        return "count_scalar"
    if CARDINALITY_INTENT_RE.search(q):
        return "singular"
    return None


def is_limit_n_list_sql(query: str) -> bool:
    """True when SQL returns a multi-row list capped by LIMIT >= 2 (not a COUNT)."""
    q = query or ""
    if _COUNT_SELECT_RE.search(q):
        return False
    m = LIMIT_N_RE.search(q)
    if m is None:
        return False
    try:
        return int(m.group(1)) >= 2
    except ValueError:
        return False


def is_existence_anti_join_list_sql(query: str) -> bool:
    """LIMIT-N list over an anti-join (LEFT JOIN … IS NULL / NOT EXISTS)."""
    q = query or ""
    return is_limit_n_list_sql(q) and bool(_ANTI_JOIN_RE.search(q))


def is_existence_anti_join_count_sql(query: str) -> bool:
    """COUNT(*) that filters via anti-join / NOT EXISTS (matches existence gold)."""
    q = query or ""
    return bool(_COUNT_SELECT_RE.search(q)) and bool(_ANTI_JOIN_RE.search(q))


def check_result_shape(*, expected_grain: Grain | None, row_count: int) -> ShapeOk | ShapeMismatch:
    """Compare observed row count against expected grain (post-exec shape gate)."""
    if expected_grain in ("count_scalar", "singular") and row_count >= 2:
        return ShapeMismatch(reason="row_count_mismatch")
    return ShapeOk()


ParseOutcome = Literal["empty", "error", "rows", "unparseable"]

MAX_SHAPE_RETRIES = 1
# Consecutive LIMIT-N anti-join / existence list queries before mid-run CLARIFY.
MAX_LIST_QUERY_STREAK = 2
# count_scalar mismatches go straight to CLARIFY (no teaching retry).
IMMEDIATE_CLARIFY_GRAINS = frozenset({"count_scalar"})
SHAPE_MISMATCH_TOOL_NAME = "shape_mismatch_feedback"

_COUNT_SELECT_RE = re.compile(r"^\s*SELECT\s+COUNT\s*\(", re.IGNORECASE | re.DOTALL)
_ANTI_JOIN_RE = re.compile(
    r"(?:LEFT\s+JOIN\b[\s\S]*?\bIS\s+NULL\b)|(?:\bNOT\s+EXISTS\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedSqlToolResult:
    """Parsed sql_db_query ToolMessage payload for shape gating."""

    outcome: ParseOutcome
    row_count: int = 0


def _strip_truncation_marker(content: str) -> str:
    marker = SQL_RESULT_TRUNCATION_MARKER
    if marker in content:
        return content.split(marker, 1)[0].rstrip("\n")
    return content


def _strip_provenance_header(content: str) -> str:
    """Drop the leading ``[sql_db_query result: ...]`` line before counting rows.

    Row count below is ``len(lines) - 1`` on the assumption that line 0 is the
    column header. Leaving the provenance line in place shifts every count by one
    and the shape gate misreads results without failing.
    """
    if content.startswith(SQL_RESULT_HEADER_PREFIX):
        _, _, rest = content.partition("\n")
        return rest
    return content


def _parse_bare_tuple_rows(content: str) -> int | None:
    """Legacy fixture fallback, e.g. ``\"[(1,), (2,)]\"``."""
    stripped = content.strip()
    if not stripped.startswith("["):
        return None
    try:
        value = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        return None
    if isinstance(value, list):
        return len(value)
    return None


def parse_sql_tool_result(content: str) -> ParsedSqlToolResult:
    """Parse a sql_db_query tool payload into row_count or skip outcomes."""
    raw = content or ""
    if raw == "":
        return ParsedSqlToolResult(outcome="empty", row_count=0)
    if raw.startswith("Error:"):
        return ParsedSqlToolResult(outcome="error", row_count=0)

    text = _strip_provenance_header(_strip_truncation_marker(raw))
    if not text.strip():
        return ParsedSqlToolResult(outcome="empty", row_count=0)

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return ParsedSqlToolResult(outcome="empty", row_count=0)

    if len(lines) == 1:
        legacy = _parse_bare_tuple_rows(lines[0])
        if legacy is not None:
            return ParsedSqlToolResult(outcome="rows", row_count=legacy)
        return ParsedSqlToolResult(outcome="rows", row_count=0)

    legacy = _parse_bare_tuple_rows(text)
    if legacy is not None:
        return ParsedSqlToolResult(outcome="rows", row_count=legacy)
    return ParsedSqlToolResult(outcome="rows", row_count=len(lines) - 1)


def first_human_question(messages: list[Any]) -> str:
    """Original Ask text — first HumanMessage only (not clarify replies)."""
    for msg in messages:
        if isinstance(msg, HumanMessage):
            return str(msg.content or "")
    return ""


def _tool_message_name(msg: ToolMessage) -> str | None:
    return getattr(msg, "name", None)


def latest_sql_db_query_tool_message(messages: list[Any]) -> ToolMessage | None:
    """Latest sql_db_query ToolMessage by message order."""
    latest: ToolMessage | None = None
    for msg in messages:
        if isinstance(msg, ToolMessage) and _tool_message_name(msg) == "sql_db_query":
            latest = msg
    return latest


def latest_sql_db_query_sql(messages: list[Any]) -> str | None:
    """SQL text from the tool_call that produced the latest sql_db_query ToolMessage."""
    tool_msg = latest_sql_db_query_tool_message(messages)
    if tool_msg is None:
        return None
    call_id = getattr(tool_msg, "tool_call_id", None)
    if not call_id:
        return None
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        for tc in getattr(msg, "tool_calls", None) or []:
            if tc.get("id") == call_id and tc.get("name") == "sql_db_query":
                args = tc.get("args") or {}
                query = args.get("query")
                return str(query) if query is not None else None
    return None


def current_hop_has_sql_db_query(messages: list[Any]) -> bool:
    """True when the most recent tool-calling hop produced a sql_db_query result."""
    last_ai_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            last_ai_idx = i
            break
    if last_ai_idx is None:
        return False
    for msg in messages[last_ai_idx + 1 :]:
        if isinstance(msg, ToolMessage) and _tool_message_name(msg) == "sql_db_query":
            return True
    return False


def build_shape_mismatch_feedback(
    *,
    expected_grain: Grain,
    observed_row_count: int,
    shape_retry_index: int,
) -> tuple[AIMessage, ToolMessage]:
    tool_call_id = f"shape_mismatch_{shape_retry_index}"
    payload = {
        "kind": "shape_mismatch",
        "reason": "row_count_mismatch",
        "expected_grain": expected_grain,
        "observed_row_count": observed_row_count,
        "shape_retry_index": shape_retry_index,
    }
    ai_msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": SHAPE_MISMATCH_TOOL_NAME,
                "args": payload,
                "id": tool_call_id,
                "type": "tool_call",
            }
        ],
    )
    tool_msg = ToolMessage(
        content=json.dumps(payload),
        tool_call_id=tool_call_id,
        name=SHAPE_MISMATCH_TOOL_NAME,
    )
    return ai_msg, tool_msg


def build_shape_clarify_message(*, expected_grain: Grain, observed_row_count: int) -> AIMessage:
    return AIMessage(
        content=(
            f"{CLARIFY_PREFIX} result shape mismatch — expected "
            f"{expected_grain} but query returned {observed_row_count} rows."
        )
    )


@dataclass(frozen=True)
class ShapeGateOutcome:
    messages: tuple[BaseMessage, ...] = ()
    shape_retries: int = 0
    list_query_streak: int = 0
    fingerprint_seen: Any = frozenset()

    def to_state_update(self) -> dict[str, Any]:
        return {
            "messages": list(self.messages),
            "shape_retries": self.shape_retries,
            "list_query_streak": self.list_query_streak,
            "fingerprint_seen": self.fingerprint_seen,
        }


@dataclass(frozen=True)
class HopObservation:
    has_query: bool
    tool_content: str = ""
    sql: str = ""
    question: str = ""
    parsed: ParsedSqlToolResult = ParsedSqlToolResult(outcome="empty", row_count=0)
    grain: Grain | None = None
    shape_retries: int = 0
    list_query_streak: int = 0
    fingerprint_seen: dict[str, Any] | frozenset[str] = frozenset()


def observe_hop(state: dict[str, Any]) -> HopObservation:
    from app.eval.sql.agent.no_progress import observe_sql_execution

    messages = list(state.get("messages") or [])
    shape_retries = int(state.get("shape_retries") or 0)
    list_streak = int(state.get("list_query_streak") or 0)
    fingerprint_dict = dict(state.get("fingerprint_seen") or {})

    if not current_hop_has_sql_db_query(messages):
        return HopObservation(
            has_query=False,
            shape_retries=shape_retries,
            list_query_streak=list_streak,
            fingerprint_seen=fingerprint_dict,
        )

    tool_msg = latest_sql_db_query_tool_message(messages)
    if tool_msg is None:
        return HopObservation(
            has_query=False,
            shape_retries=shape_retries,
            list_query_streak=list_streak,
            fingerprint_seen=fingerprint_dict,
        )

    tool_content = str(tool_msg.content or "")
    parsed = parse_sql_tool_result(tool_content)
    if parsed.outcome in {"empty", "error", "unparseable"}:
        return HopObservation(
            has_query=False,
            shape_retries=shape_retries,
            list_query_streak=list_streak,
            fingerprint_seen=fingerprint_dict,
        )

    sql = latest_sql_db_query_sql(messages) or ""
    truncated = SQL_RESULT_TRUNCATION_MARKER in tool_content
    fingerprint_seen = observe_sql_execution(
        fingerprint_dict,
        sql,
        row_count=parsed.row_count,
        truncated=truncated,
    )
    question = first_human_question(messages)
    grain = expected_result_grain(question)

    return HopObservation(
        has_query=True,
        tool_content=tool_content,
        sql=sql,
        question=question,
        parsed=parsed,
        grain=grain,
        shape_retries=shape_retries,
        list_query_streak=list_streak,
        fingerprint_seen=fingerprint_seen,
    )


def existence_streak_decision(obs: HopObservation) -> tuple[int, AIMessage | None]:
    from app.eval.sql.agent.budget_clarify import build_count_vs_list_clarify_message

    list_streak = obs.list_query_streak
    if is_existence_anti_join_count_sql(obs.sql):
        return 0, None

    if (
        obs.parsed.outcome == "rows"
        and obs.parsed.row_count >= 2
        and is_existence_anti_join_list_sql(obs.sql)
    ):
        list_streak += 1
        if list_streak >= MAX_LIST_QUERY_STREAK:
            clarify = build_count_vs_list_clarify_message(
                obs.question,
                grain="count_scalar",
                budget_exhausted=False,
            )
            return list_streak, clarify
        return list_streak, None

    return list_streak, None


def grain_decision(obs: HopObservation, list_streak: int) -> ShapeGateOutcome:
    shape_result = check_result_shape(expected_grain=obs.grain, row_count=obs.parsed.row_count)
    if not isinstance(shape_result, ShapeMismatch):
        return ShapeGateOutcome(
            shape_retries=0,
            list_query_streak=list_streak,
            fingerprint_seen=obs.fingerprint_seen,
        )

    allow_retry = (
        obs.shape_retries < MAX_SHAPE_RETRIES
        and obs.grain is not None
        and obs.grain not in IMMEDIATE_CLARIFY_GRAINS
    )
    if allow_retry:
        ai_feedback, tool_feedback = build_shape_mismatch_feedback(
            expected_grain=obs.grain,
            observed_row_count=obs.parsed.row_count,
            shape_retry_index=obs.shape_retries,
        )
        return ShapeGateOutcome(
            messages=(ai_feedback, tool_feedback),
            shape_retries=obs.shape_retries + 1,
            list_query_streak=list_streak,
            fingerprint_seen=obs.fingerprint_seen,
        )

    clarify = build_shape_clarify_message(
        expected_grain=obs.grain or "count_scalar",
        observed_row_count=obs.parsed.row_count,
    )
    return ShapeGateOutcome(
        messages=(clarify,),
        shape_retries=obs.shape_retries,
        list_query_streak=list_streak,
        fingerprint_seen=obs.fingerprint_seen,
    )


def decide_shape_gate(state: dict[str, Any]) -> ShapeGateOutcome:
    obs = observe_hop(state)
    if not obs.has_query:
        return ShapeGateOutcome(
            shape_retries=0,
            list_query_streak=obs.list_query_streak,
            fingerprint_seen=obs.fingerprint_seen,
        )

    list_streak, clarify_msg = existence_streak_decision(obs)
    if clarify_msg is not None:
        return ShapeGateOutcome(
            messages=(clarify_msg,),
            shape_retries=0,
            list_query_streak=0,
            fingerprint_seen=obs.fingerprint_seen,
        )

    return grain_decision(obs, list_streak)
