"""Small live product-path smoke for auth, tier isolation, and rollback routing."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt

from app.config import settings
from app.experiments.arming import require_armed
from app.experiments.live_errors import LiveAskSmokeError
from app.experiments.spend_coordinator import reserved
from app.providers.model_purpose import ModelPurpose
from app.providers.route_settings import resolve_model_route
from app.rag.access_tiers import ALL_TIERS

API_SMOKE_LIVE_ENV = "DEEPSEEK_API_SMOKE_LIVE"
ALLOWED_SALES_TIERS = {"all", "sales"}
API_SMOKE_RESERVATION_USD = 0.10


def require_redis_ready(*, ping_fn: Callable[[], bool] | None = None) -> None:
    """Refuse the live API smoke before model spend when JWT replay storage is down."""
    if ping_fn is None:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            ready = bool(client.ping())
        except Exception as exc:
            raise LiveAskSmokeError(f"Redis preflight failed: {exc}") from exc
        finally:
            client.close()
    else:
        ready = bool(ping_fn())
    if not ready:
        raise LiveAskSmokeError("Redis preflight failed: ping returned false")


def run_ask_smoke(
    *,
    post_fn: Callable[..., Any],
    token: str,
    default_route_fn: Callable[[], str],
) -> dict[str, Any]:
    """Exercise rejected auth plus one authorized request against independent tier rules."""
    question = "What number should sales use for unusual quote requests?"
    invalid = post_fn(
        "/api/ask",
        json={"question": question},
        headers={"Authorization": "Bearer invalid"},
    )
    valid = post_fn(
        "/api/ask",
        json={"question": question},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = valid.json() if valid.status_code == 200 else {}
    sources = body.get("sources", [])
    source_tiers = [str(source.get("access_tier") or "") for source in sources]
    source_paths = [
        str(source.get("source_file") or source.get("source") or "") for source in sources
    ]
    denied_tiers_seen = sorted(
        tier for tier in source_tiers if tier and tier not in ALLOWED_SALES_TIERS
    )
    denied_path_tiers = set(ALL_TIERS) - ALLOWED_SALES_TIERS
    denied_source_paths: list[str] = []
    sales_source_seen = False
    for source, tier, path in zip(sources, source_tiers, source_paths, strict=True):
        del source
        path_parts = {part.casefold() for part in path.replace("\\", "/").split("/") if part}
        route_proven = tier in ALLOWED_SALES_TIERS or bool(path_parts & ALLOWED_SALES_TIERS)
        sales_source_seen = sales_source_seen or tier == "sales" or "sales" in path_parts
        if path_parts & denied_path_tiers or not route_proven:
            denied_source_paths.append(path or "<missing-source-path>")
    default_route = default_route_fn()
    expected_rag_deployment = resolve_model_route(ModelPurpose.rag_answer).deployment
    answer = str(body.get("answer") or "")
    passed = (
        invalid.status_code in {401, 403}
        and valid.status_code == 200
        and bool(answer.strip())
        and bool(sources)
        and not denied_tiers_seen
        and not denied_source_paths
        and sales_source_seen
        and default_route == expected_rag_deployment
        and body.get("model") == expected_rag_deployment
        and "reasoning_content" not in answer.casefold()
    )
    return {
        "passed": passed,
        "invalid_auth_status": invalid.status_code,
        "valid_status": valid.status_code,
        "default_route": default_route,
        "response_model": body.get("model"),
        "source_tiers": source_tiers,
        "denied_tiers_seen": denied_tiers_seen,
        "source_paths": source_paths,
        "denied_source_paths": denied_source_paths,
        "sales_source_seen": sales_source_seen,
        "source_count": len(sources),
    }


def _sales_token() -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": settings.rag_jwt_iss,
            "aud": settings.rag_jwt_aud,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=2)).timestamp()),
            "user_id": 1,
            "role": "sales",
            "permissions": ["view invoice"],
            "department_id": None,
            "jti": uuid.uuid4().hex,
            "scope": "ask",
        },
        settings.rag_jwt_secret,
        algorithm="HS256",
    )


def run_live_ask_smoke(*, output_path: Path, ledger_path: Path | None = None) -> dict[str, Any]:
    require_armed(API_SMOKE_LIVE_ENV, purpose="live /api/ask smoke")
    if output_path.exists():
        raise LiveAskSmokeError(f"smoke evidence already exists: {output_path}")
    require_redis_ready()
    from fastapi.testclient import TestClient

    from app.main import app

    rag_deployment = resolve_model_route(ModelPurpose.rag_answer).deployment
    with reserved(
        "api_smoke",
        rag_deployment,
        API_SMOKE_RESERVATION_USD,
        ledger_path=ledger_path,
    ) as handle:
        with TestClient(app) as client:
            handle.mark_spend_started()
            result = run_ask_smoke(
                post_fn=client.post,
                token=_sales_token(),
                default_route_fn=lambda: resolve_model_route(ModelPurpose.rag_answer).deployment,
            )
    evidence = {**result, "recorded_at": datetime.now(UTC).isoformat()}
    evidence["accounted_spend_usd"] = API_SMOKE_RESERVATION_USD
    evidence["spend_basis"] = "conservative fixed smoke reservation"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as file_handle:
        json.dump(evidence, file_handle, ensure_ascii=False, indent=2, sort_keys=True)
        file_handle.write("\n")
    if not result["passed"]:
        raise LiveAskSmokeError(f"live /api/ask smoke failed: {result}")
    return evidence
