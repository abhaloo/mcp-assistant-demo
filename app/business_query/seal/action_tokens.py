"""Action Tokens & Lifecycle.

Implements typed HMAC-signed action tokens for pagination and cursor continuation
with signature verification, principal reauthorization, and lifecycle expiration.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import Principal
from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_SECRET = settings.rag_jwt_secret
_TTL_MINUTES = 15


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _compute_token_signature(token_dict: dict[str, Any], secret: str) -> str:
    payload_to_sign = {k: v for k, v in token_dict.items() if k != "signature"}
    data = json.dumps(
        payload_to_sign,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    ).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).digest()
    return _b64url_encode(sig)


class BaseActionToken(BaseModel):
    """Base class for HMAC-signed action tokens supporting reauthorization and TTL."""

    model_config = ConfigDict(extra="forbid")

    principal: str | int
    entity_id: str | int | None = None
    department_id: str | int | None = None
    policy_hash: str = ""
    bundle_hash: str = ""
    expires_at: datetime
    signature: str = ""

    @field_validator("principal", mode="before")
    @classmethod
    def _normalize_principal(cls, value: object) -> object:
        if isinstance(value, Principal):
            return value.user_id
        return value

    def sign(self, secret: str | None = None) -> Self:
        sec = secret or _DEFAULT_SECRET
        sig = _compute_token_signature(self.model_dump(mode="json"), sec)
        return self.model_copy(update={"signature": sig})

    def verify_signature(self, secret: str | None = None) -> bool:
        if not self.signature:
            return False
        sec = secret or _DEFAULT_SECRET
        expected = _compute_token_signature(self.model_dump(mode="json"), sec)
        return hmac.compare_digest(self.signature, expected)

    def is_expired(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(tz=UTC)
        return current > self.expires_at

    def reauthorize(self, principal: Principal) -> bool:
        user_id_str = str(principal.user_id)
        token_user_id = str(self.principal)
        if user_id_str != token_user_id:
            return False
        if self.entity_id is not None and str(principal.entity_id) != str(self.entity_id):
            return False
        if self.department_id is not None:
            principal_dept = (
                principal.scope_values.department_id
                if principal.scope_values and principal.scope_values.department_id is not None
                else principal.department_id
            )
            if principal_dept is not None and str(principal_dept) != str(self.department_id):
                return False
        if (
            self.policy_hash
            and principal.manifest_hash
            and self.policy_hash != principal.manifest_hash
        ):
            return False
        return True

    def encode(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return _b64url_encode(payload)

    @classmethod
    def decode(cls, raw: str) -> Self:
        if raw.startswith("{"):
            data = json.loads(raw)
        else:
            decoded_bytes = _b64url_decode(raw)
            data = json.loads(decoded_bytes.decode("utf-8"))
        return cls.model_validate(data)

    @classmethod
    def mint(
        cls,
        *,
        principal: Principal | str | int,
        secret: str | None = None,
        now: datetime | None = None,
        expires_at: datetime | None = None,
        **extra_fields: Any,
    ) -> Self:
        signing_secret = secret or _DEFAULT_SECRET
        if not signing_secret:
            raise ValueError("Signing secret is required to mint action tokens")
        current_time = now or datetime.now(tz=UTC)
        token_expiry = expires_at or (current_time + timedelta(minutes=_TTL_MINUTES))

        entity_id = extra_fields.pop("entity_id", None)
        department_id = extra_fields.pop("department_id", None)
        policy_hash = extra_fields.pop("policy_hash", "")
        bundle_hash = extra_fields.pop("bundle_hash", "")

        if isinstance(principal, Principal):
            p_val = principal.user_id
            if entity_id is None:
                entity_id = principal.entity_id
            if department_id is None:
                department_id = (
                    principal.scope_values.department_id
                    if principal.scope_values
                    else principal.department_id
                )
            if not policy_hash and principal.manifest_hash:
                policy_hash = principal.manifest_hash
        else:
            p_val = principal

        instance = cls(
            principal=p_val,
            entity_id=entity_id,
            department_id=department_id,
            policy_hash=policy_hash,
            bundle_hash=bundle_hash,
            expires_at=token_expiry,
            signature="",
            **extra_fields,
        )
        return instance.sign(signing_secret)


class ResultPageCursor(BaseActionToken):
    """HMAC-signed result page cursor for keyset pagination."""

    token_type: Literal["result_page_cursor"] = "result_page_cursor"
    project_id: str = Field(default="default", min_length=1)
    answer_query_id: str
    plan_answer_query_id: str | None = None
    plan_fingerprint: str
    keyset_position: dict[str, Any] = Field(default_factory=dict)
    page_size: int = Field(default=20, ge=1, le=50)
    total_row_count: int | None = None
    offset_row_count: int = Field(default=0, ge=0)

    @classmethod
    def mint(
        cls,
        *,
        principal: Principal | str | int,
        entity_id: str | int | None = None,
        department_id: str | int | None = None,
        policy_hash: str = "",
        bundle_hash: str = "",
        project_id: str = "default",
        answer_query_id: str,
        plan_answer_query_id: str | None = None,
        plan_fingerprint: str,
        keyset_position: dict[str, Any] | None = None,
        page_size: int = 20,
        expires_at: datetime | None = None,
        total_row_count: int | None = None,
        offset_row_count: int = 0,
        secret: str | None = None,
        now: datetime | None = None,
    ) -> ResultPageCursor:
        return super().mint(
            principal=principal,
            entity_id=entity_id,
            department_id=department_id,
            policy_hash=policy_hash,
            bundle_hash=bundle_hash,
            project_id=project_id,
            answer_query_id=answer_query_id,
            plan_answer_query_id=plan_answer_query_id,
            plan_fingerprint=plan_fingerprint,
            keyset_position=dict(keyset_position or {}),
            page_size=page_size,
            expires_at=expires_at,
            total_row_count=total_row_count,
            offset_row_count=offset_row_count,
            secret=secret,
            now=now,
        )
