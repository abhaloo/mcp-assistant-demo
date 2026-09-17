#!/usr/bin/env python3
"""Mint a demo JWT for POST /api/ask (no eval harness dependency)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import jwt

REPO_ROOT = Path(__file__).resolve().parents[1]
PRINCIPALS_PATH = REPO_ROOT / "demo_principals.json"


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path)


def _load_manifest_hash() -> str:
    index_path = REPO_ROOT / "app" / "policy" / "manifest" / "index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    return str(payload["current"])


def _record_access_from_principal(principal: dict[str, object]) -> dict[str, object]:
    frozen = principal.get("record_access")
    if isinstance(frozen, dict):
        claim = dict(frozen)
        claim["manifest_hash"] = _load_manifest_hash()
        return claim

    permissions = list(principal["permissions"])
    resources = principal.get("resources") or {}
    return {
        "schema_version": 2,
        "manifest_hash": _load_manifest_hash(),
        "entity_id": int(principal["entity_id"]),
        "cross_entity": False,
        "document_tiers": _document_tiers(permissions),
        "scope_values": {"department_id": principal.get("department_id")},
        "resources": {
            name: {
                "actions": list(entry["actions"] if isinstance(entry, dict) else entry),
                "field_sets": ["summary", "detail"],
            }
            for name, entry in resources.items()
        },
    }


def _document_tiers(permissions: list[str]) -> list[str]:
    sys.path.insert(0, str(REPO_ROOT))
    from app.rag.access_tiers import get_access_tiers

    return list(get_access_tiers("user", permissions))


def mint_token(persona: str, *, ttl_seconds: int = 3600) -> str:
    principals = json.loads(PRINCIPALS_PATH.read_text(encoding="utf-8"))
    if persona not in principals:
        raise SystemExit(f"Unknown persona {persona!r}. Choose: {', '.join(sorted(principals))}")

    principal = principals[persona]
    secret = os.environ.get("RAG_JWT_SECRET")
    if not secret:
        raise SystemExit(
            "RAG_JWT_SECRET is not set. Copy .env.example to .env or export the variable."
        )
    iss = os.environ.get("RAG_JWT_ISS", "laravel-billing")
    aud = os.environ.get("RAG_JWT_AUD", "rag-service")
    now = int(time.time())

    claims: dict[str, object] = {
        "iss": iss,
        "aud": aud,
        "iat": now,
        "exp": now + ttl_seconds,
        "user_id": int(principal["user_id"]),
        "role": principal.get("role", "user"),
        "permissions": list(principal["permissions"]),
        "department_id": principal.get("department_id"),
        "jti": uuid.uuid4().hex,
        "scope": "ask",
        "record_access": _record_access_from_principal(principal),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def main() -> None:
    _load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--persona",
        required=True,
        choices=["demo_finance", "demo_production"],
    )
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    args = parser.parse_args()
    print(mint_token(args.persona, ttl_seconds=args.ttl_seconds))


if __name__ == "__main__":
    main()
