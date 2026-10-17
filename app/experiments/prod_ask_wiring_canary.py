"""Opt-in local prod Ask wiring canary (JSON + SSE via TestClient)."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt

from app.config import settings
from app.experiments.arming import require_armed
from app.experiments.live_ask_tier_smoke import require_redis_ready
from app.experiments.live_errors import LiveAskSmokeError
from app.experiments.spend_coordinator import reserved
from app.providers.model_purpose import ModelPurpose
from app.providers.route_policy import RouteContext, get_route_policy
from app.rag.model_router import is_hard_financial

PROD_ASK_WIRING_CANARY_LIVE_ENV = "RUN_PROD_ASK_WIRING_CANARY"
PROD_ASK_WIRING_CANARY_RESERVATION_USD = 3.0
PROD_ASK_WIRING_CANARY_PHASE = "prod_ask_wiring_canary"

WIRING_CASES: tuple[dict[str, str], ...] = (
    {
        "id": "semantic",
        "question": "What number should sales use for unusual quote requests?",
        "expected_query_type": "semantic",
    },
    {
        "id": "structured_non_hard",
        "question": "How many customers are in the database?",
        "expected_query_type": "structured",
    },
    {
        "id": "hard_financial",
        "question": "what is our total outstanding balance?",
        "expected_query_type": "structured",
    },
)

CANARY_ENTITY_ID = 1
CANARY_PERMISSIONS = (
    "view invoice",
    "view job",
    "view customer order",
    "view customer",
    "view journal entries",
    "view inventory",
    "view quotation",
)


def _canary_record_access() -> dict[str, Any]:
    """The v2 ``record_access`` claim the canary presents."""
    from app.policy.manifest_loader import manifest_content_hash

    return {
        "schema_version": 2,
        "manifest_hash": manifest_content_hash(),
        "entity_id": CANARY_ENTITY_ID,
        "cross_entity": False,
        "document_tiers": ["all"],
        "scope_values": {},
        "resources": {},
    }


def _admin_token() -> str:
    """Mint a one-time admin JWT (fresh ``jti``) for a single remote Ask call."""
    secret = (settings.rag_jwt_secret or "").strip()
    if not secret:
        raise LiveAskSmokeError("RAG_JWT_SECRET is required to mint the canary admin token")
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": settings.rag_jwt_iss,
            "aud": settings.rag_jwt_aud,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),
            "user_id": 1,
            "role": "admin",
            "permissions": list(CANARY_PERMISSIONS),
            "department_id": None,
            "jti": uuid.uuid4().hex,
            "scope": "ask",
            "record_access": _canary_record_access(),
        },
        secret,
        algorithm="HS256",
    )


def _local_route_env_snapshot() -> dict[str, str | None]:
    """Record local ASK_MODEL_ROUTE_* overrides that feed the catalog oracle."""
    keys = (
        "ASK_MODEL_ROUTE_CLASSIFY",
        "ASK_MODEL_ROUTE_RAG_ANSWER",
        "ASK_MODEL_ROUTE_SQL_AGENT",
        "ASK_MODEL_ROUTE_CONVERSATION",
        "ASK_MODEL_ROUTE_RECORD_REASONING",
        "ASK_MODEL_ROUTE_SQL_AGENT_HARD_FINANCIAL",
    )
    return {key: os.environ.get(key) for key in keys}


def _infer_query_type_from_stages(stage_models: dict[str, Any] | None) -> str | None:
    """Infer Ask path from Stage Model Report (SSE done has no query_type)."""
    rag = _stage_entry(stage_models if isinstance(stage_models, dict) else {}, "rag_answer")
    sql = _stage_entry(stage_models if isinstance(stage_models, dict) else {}, "sql_agent")
    if sql.get("status") == "used" and rag.get("status") != "used":
        return "structured"
    if rag.get("status") == "used" and sql.get("status") != "used":
        return "semantic"
    return None


def _expected_final_deployment(case_id: str, question: str) -> str:
    """Catalog oracle for the final-producer deployment of a wiring case."""
    policy = get_route_policy()
    if case_id == "semantic":
        return policy.resolve(ModelPurpose.rag_answer).deployment
    return policy.resolve(
        ModelPurpose.sql_agent,
        RouteContext(question=question),
    ).deployment


def _stage_entry(stage_models: dict[str, Any] | None, purpose: str) -> dict[str, Any]:
    if not isinstance(stage_models, dict):
        return {}
    entry = stage_models.get(purpose)
    return entry if isinstance(entry, dict) else {}


def evaluate_wiring_body(
    *,
    case_id: str,
    question: str,
    expected_query_type: str,
    status_code: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Score one Ask JSON/SSE-done body against catalog RoutePolicy wiring oracles."""
    expected_model = _expected_final_deployment(case_id, question)
    stage_models = body.get("stage_models") if isinstance(body.get("stage_models"), dict) else {}
    query_type = body.get("query_type")
    response_model = body.get("model")
    answer = str(body.get("answer") or "")
    rag = _stage_entry(stage_models, "rag_answer")
    sql = _stage_entry(stage_models, "sql_agent")
    classify = _stage_entry(stage_models, "classify")

    if case_id == "semantic":
        producer_ok = (
            response_model == expected_model
            and rag.get("status") == "used"
            and rag.get("requested_deployment") == expected_model
            and sql.get("status") == "unused"
        )
    else:
        producer_ok = (
            response_model == expected_model
            and sql.get("status") == "used"
            and sql.get("requested_deployment") == expected_model
            and rag.get("status") == "unused"
        )

    passed = (
        status_code == 200
        and query_type == expected_query_type
        and bool(answer.strip())
        and classify.get("status") == "used"
        and producer_ok
        and "reasoning_content" not in answer.casefold()
    )
    return {
        "passed": passed,
        "case_id": case_id,
        "question": question,
        "status_code": status_code,
        "query_type": query_type,
        "expected_query_type": expected_query_type,
        "response_model": response_model,
        "expected_model": expected_model,
        "classify_status": classify.get("status"),
        "rag_status": rag.get("status"),
        "rag_deployment": rag.get("requested_deployment"),
        "sql_status": sql.get("status"),
        "sql_deployment": sql.get("requested_deployment"),
        "answer_nonempty": bool(answer.strip()),
    }


def parse_sse_events(text: str) -> list[tuple[str, Any]]:
    """Parse named SSE frames into ``(event_name, data)`` pairs."""
    events: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.splitlines() if line.strip() and not line.startswith(":")]
        if not lines:
            continue
        event_name: str | None = None
        data_lines: list[str] = []
        for line in lines:
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        if event_name is None or not data_lines:
            continue
        payload = "\n".join(data_lines)
        try:
            parsed: Any = json.loads(payload)
        except json.JSONDecodeError:
            parsed = payload
        events.append((event_name, parsed))
    return events


def parse_sse_done(text: str) -> dict[str, Any]:
    """Extract the JSON payload from the SSE ``event: done`` frame."""
    for event_name, data in parse_sse_events(text):
        if event_name != "done":
            continue
        if not isinstance(data, dict):
            raise LiveAskSmokeError("SSE done payload was not a JSON object")
        return data
    raise LiveAskSmokeError("SSE stream missing event: done payload")


def _sse_answer_text(events: list[tuple[str, Any]]) -> str:
    """Reconstruct answer text from ``token`` events (done has no answer field)."""
    pieces: list[str] = []
    for event_name, data in events:
        if event_name != "token" or not isinstance(data, dict):
            continue
        piece = data.get("d")
        if piece is not None:
            pieces.append(str(piece))
    return "".join(pieces)


def run_wiring_case_json(
    *,
    post_fn: Callable[..., Any],
    token_fn: Callable[[], str],
    case_id: str,
    question: str,
    expected_query_type: str,
) -> dict[str, Any]:
    """POST /api/ask as JSON and score RoutePolicy wiring."""
    response = post_fn(
        "/api/ask",
        json={"question": question},
        headers={
            "Authorization": f"Bearer {token_fn()}",
            "Accept": "application/json",
        },
    )
    body = response.json() if response.status_code == 200 else {}
    if not isinstance(body, dict):
        body = {}
    result = evaluate_wiring_body(
        case_id=case_id,
        question=question,
        expected_query_type=expected_query_type,
        status_code=response.status_code,
        body=body,
    )
    result["transport"] = "json"
    return result


def run_wiring_case_sse(
    *,
    post_fn: Callable[..., Any],
    token_fn: Callable[[], str],
    case_id: str,
    question: str,
    expected_query_type: str,
) -> dict[str, Any]:
    """POST /api/ask SSE; score done model/stages + token-reconstructed answer."""
    response = post_fn(
        "/api/ask",
        json={"question": question},
        headers={
            "Authorization": f"Bearer {token_fn()}",
            "Accept": "text/event-stream",
        },
    )
    body: dict[str, Any] = {}
    parse_error: str | None = None
    if response.status_code == 200:
        try:
            events = parse_sse_events(response.text)
            done = next((data for name, data in events if name == "done"), None)
            if not isinstance(done, dict):
                raise LiveAskSmokeError("SSE stream missing event: done payload")
            inferred = _infer_query_type_from_stages(done.get("stage_models"))
            body = {
                **done,
                "answer": _sse_answer_text(events),
                "query_type": inferred,
            }
        except (LiveAskSmokeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
            body = {}
    result = evaluate_wiring_body(
        case_id=case_id,
        question=question,
        expected_query_type=expected_query_type,
        status_code=response.status_code,
        body=body,
    )
    result["transport"] = "sse"
    if parse_error is not None:
        result["passed"] = False
        result["sse_parse_error"] = parse_error
    return result


def _validate_wiring_fixtures() -> None:
    """Fail closed if fixed questions no longer match hard-financial regex intent."""
    non_hard = next(c for c in WIRING_CASES if c["id"] == "structured_non_hard")
    hard = next(c for c in WIRING_CASES if c["id"] == "hard_financial")
    if is_hard_financial(non_hard["question"]):
        raise LiveAskSmokeError(
            f"structured_non_hard fixture unexpectedly matches hard_financial: "
            f"{non_hard['question']!r}"
        )
    if not is_hard_financial(hard["question"]):
        raise LiveAskSmokeError(
            f"hard_financial fixture no longer matches hard_financial: {hard['question']!r}"
        )


def run_prod_ask_wiring_canary(
    *,
    output_path: Path,
    ledger_path: Path | None = None,
) -> dict[str, Any]:
    """Opt-in local Ask wiring canary: 3 JSON + 3 SSE via in-process TestClient."""
    require_armed(PROD_ASK_WIRING_CANARY_LIVE_ENV, purpose="prod Ask wiring canary")
    if output_path.exists():
        raise LiveAskSmokeError(f"canary evidence already exists: {output_path}")
    _validate_wiring_fixtures()
    require_redis_ready()

    from fastapi.testclient import TestClient

    from app.main import app

    case_results: list[dict[str, Any]] = []
    invalid_auth_status: int | None = None

    with reserved(
        PROD_ASK_WIRING_CANARY_PHASE,
        "gpt-4o-mini",
        PROD_ASK_WIRING_CANARY_RESERVATION_USD,
        ledger_path=ledger_path,
    ) as handle:
        with TestClient(app) as client:

            def post_fn(path: str, *, json: dict[str, Any], headers: dict[str, str]):
                return client.post(path, json=json, headers=headers)

            invalid = post_fn(
                "/api/ask",
                json={"question": WIRING_CASES[0]["question"]},
                headers={"Authorization": "Bearer invalid", "Accept": "application/json"},
            )
            invalid_auth_status = invalid.status_code
            if invalid_auth_status not in {401, 403}:
                raise LiveAskSmokeError(
                    f"local invalid JWT expected 401/403, got {invalid_auth_status}"
                )

            for case in WIRING_CASES:
                handle.mark_spend_started()
                case_results.append(
                    run_wiring_case_json(
                        post_fn=post_fn,
                        token_fn=_admin_token,
                        case_id=case["id"],
                        question=case["question"],
                        expected_query_type=case["expected_query_type"],
                    )
                )
                case_results.append(
                    run_wiring_case_sse(
                        post_fn=post_fn,
                        token_fn=_admin_token,
                        case_id=case["id"],
                        question=case["question"],
                        expected_query_type=case["expected_query_type"],
                    )
                )

    passed = all(item.get("passed") is True for item in case_results) and invalid_auth_status in {
        401,
        403,
    }
    evidence: dict[str, Any] = {
        "passed": passed,
        "transport": "local_testclient",
        "invalid_auth_status": invalid_auth_status,
        "cases": case_results,
        "local_route_env": _local_route_env_snapshot(),
        "recorded_at": datetime.now(UTC).isoformat(),
        "accounted_spend_usd": PROD_ASK_WIRING_CANARY_RESERVATION_USD,
        "spend_basis": "conservative fixed wiring canary reservation",
        "evidence_layer": "INTEGRATED_local_ask_testclient",
        "harness_status": "scaffolding",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as file_handle:
        json.dump(evidence, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
        file_handle.write("\n")
    if not passed:
        raise LiveAskSmokeError(f"prod Ask wiring canary failed: {evidence}")
    return evidence
