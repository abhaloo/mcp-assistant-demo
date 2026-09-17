"""
v2 authorization-snapshot claim shape (``record_access``) — Billing -> RAG
signed JWT claim (task A2, "Ask AI context/access parity", Phase 2).

Strictly validated, fail-closed: any deviation from the exact binding
contract below raises ``pydantic.ValidationError``. The JWT layer
(``app/auth/jwt.py``) converts that into the existing 401 auth error — a
malformed v2 claim must never be treated as (or silently degrade to) a v1
token. All models use ``strict=True`` so type checks reject cross-type
coercion outright (e.g. ``True`` for an int field, ``"2"`` for
``schema_version``) rather than silently accepting it.

Binding contract (exact values — task-A2-brief.md, verbatim from the
auditable plan's SS6.2):

    record_access:
      schema_version: 2
      manifest_hash: "sha256:..."
      entity_id: 1
      cross_entity: false
      document_tiers: [all, sales]
      scope_values:
        department_id: 4
      resources:
        quotation:
          actions: [search, read, link]
          field_sets: [summary, detail]
        product:
          actions: [search, read]
          field_sets: [summary]

Feature-off scope note: this module only validates the CLAIM SHAPE. It does
not verify ``manifest_hash`` against a deployed manifest (Phase 2 step 6 /
v2.1) and no v2-gated feature reads these fields yet — see task-A2-brief.md.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MANIFEST_HASH_PATTERN = r"^sha256:[0-9a-fA-F]{64}$"

# field_sets entries and document_tiers entries must be non-empty strings.
_NonEmptyStr = Annotated[str, Field(min_length=1)]

ResourceAction = Literal["search", "read", "link"]


class ScopeValues(BaseModel):
    """Signed row-scope predicates. Only ``department_id`` is recognized —
    any other key fails closed via ``extra="forbid"``."""

    model_config = ConfigDict(strict=True, extra="forbid")

    department_id: int | None = None


class ResourceGrant(BaseModel):
    """Per-resource capability granted by the v2 snapshot: which actions are
    allowed and which named field sets are visible."""

    model_config = ConfigDict(strict=True, extra="forbid")

    actions: list[ResourceAction]
    field_sets: list[_NonEmptyStr]


class RecordAccess(BaseModel):
    """Strictly validated v2 ``record_access`` claim.

    ``model_validate()`` raises ``pydantic.ValidationError`` for every case
    in the R1 fail-closed matrix: unknown ``schema_version``, missing or
    malformed ``manifest_hash``, malformed ``entity_id``/``cross_entity``/
    ``document_tiers``/``scope_values`` types, unknown keys inside
    ``scope_values``, and malformed ``resources`` entries.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[2]
    manifest_hash: Annotated[str, Field(pattern=_MANIFEST_HASH_PATTERN)]
    entity_id: int
    cross_entity: bool
    document_tiers: list[_NonEmptyStr]
    scope_values: ScopeValues
    resources: dict[str, ResourceGrant]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version_is_a_plain_int(cls, value: object) -> object:
        # Literal[2] alone accepts 2.0 (float) via loose "==" equality even
        # under strict=True — bool/float must be rejected before that check
        # runs, or "schema_version": 2.0 would silently pass as v2.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("schema_version must be exactly 2 (int)")
        return value
