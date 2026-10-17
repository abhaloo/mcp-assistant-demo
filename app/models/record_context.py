"""Strict v1 ``record_context`` request-body shape and canonical digest recipe.

Digest binding (the JWT ``record_context_digest`` claim) and enforcement live
in ``app/auth/jwt.py`` — this module supplies the shape and canonical serialization.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ResourceType = Literal[
    "invoice",
    "quotation",
    "payable_quotation",
    "credit_note",
    "customer_order",
    "job",
    "customer",
    "supplier",
    "product",
    "inventory",
]

# Binding caps (controller decision, task-A3-brief.md) — any breach REJECTs (422).
MAX_RECORDS = 20
MAX_FIELDS_PER_RECORD = 10
MAX_FIELD_KEY_LEN = 64
MAX_FIELD_VALUE_LEN = 256
MAX_LABEL_LEN = 128
MAX_TITLE_LEN = 256
MAX_RECORD_ID_LEN = 64
MAX_RECORD_CONTEXT_BYTES = 16384

_NonEmptyRecordId = Annotated[str, Field(min_length=1, max_length=MAX_RECORD_ID_LEN)]


def canonical_json_bytes(data: Any) -> bytes:
    """THE canonical-serialization recipe for ``record_context`` (Billing must mirror
    this exactly — it is the digest pre-image and the total-bytes-cap measurement).

    Recipe: ``json.dumps`` with ``sort_keys=True`` (sorts object keys at every
    nesting level, not just the top one — a stdlib guarantee), ``separators=(",", ":")``
    (strips all whitespace: no space after ``,`` or ``:``), ``ensure_ascii=False``
    (non-ASCII characters are emitted literally, not ``\\uXXXX``-escaped),
    ``allow_nan=False`` (reject NaN/Infinity rather than silently emitting
    non-JSON tokens), then ``.encode("utf-8")`` for the final byte sequence
    that gets hashed. List order (``records``) is preserved as-is — only
    object keys are sorted, never array elements.

    **PHP slash-escaping trap (Billing MUST match this):** Python's
    ``json.dumps`` never escapes ``/`` — a forward slash is emitted literally,
    e.g. ``"2024/001"`` stays ``"2024/001"``. PHP's ``json_encode`` escapes
    ``/`` as ``\\/`` **by default**. Any Billing mirror that calls plain
    ``json_encode($data)`` will therefore compute a *different* digest for
    any value containing a ``/`` (invoice refs like ``"2024/001"``, dates,
    URLs in ``link_key``) than this function does for the identical logical
    value — a silent 401 on every such request, with both repos' test suites
    green, because neither suite exercises a slash. Billing's mirror MUST
    call ``json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)``.
    Exact equivalences to this function's four ``json.dumps`` kwargs:

    - ``sort_keys=True`` → PHP has no built-in recursive key-sort flag; the
      mirror must recursively ``ksort()`` (or equivalent) the array at every
      nesting level *before* encoding.
    - ``separators=(",", ":")`` → PHP's ``json_encode`` already has no
      whitespace around ``,``/``:`` by default; no flag needed.
    - ``ensure_ascii=False`` → PHP's ``JSON_UNESCAPED_UNICODE`` flag (without
      it, PHP ``\\uXXXX``-escapes non-ASCII by default, same trap as the
      slash case above, just for Unicode instead of ``/``).
    - ``allow_nan=False`` → PHP's ``json_encode`` already returns ``false``
      (a hard failure, not a silent ``NaN``/``Inf`` token) for non-finite
      floats; no flag needed, but the mirror must treat that ``false`` return
      as a hard error rather than digesting an empty/false value.

    Net: the required PHP call is
    ``json_encode($sorted_data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)``
    over a recursively key-sorted array, UTF-8 bytes, matching this function
    byte-for-byte.

    **Empty ``fields`` map (Billing MUST match this too):** an empty ``fields``
    value MUST serialize as ``{}`` (a JSON *object*), never ``[]`` (a JSON
    *array*) — this function, given a Python ``{}``, always emits ``{}``. PHP
    has no distinct empty-map type: ``json_encode([])`` for an empty PHP array
    defaults to ``[]``, not ``{}``, because PHP cannot tell an empty list from
    an empty map. Billing's mirror MUST cast an empty ``fields`` value to
    ``(object)`` (or an equivalent "force object" mechanism) before encoding
    it, or the resulting bytes digest-diverge from this function's output for
    every record whose ``fields`` map happens to be empty. RAG's
    ``RecordContextRecord.fields`` model additionally rejects a wire
    ``fields: []`` outright with a 422 (it must be a JSON object, not an
    array) — so a naive PHP mirror fails two ways: outright shape rejection
    (422) if Billing ever sent the array literally, and a silent digest
    mismatch (401) on an otherwise-valid record if Billing computed its digest
    assuming ``[]`` while sending ``{}`` on the wire.

    See ``tests/fixtures/record_context_wire.json``'s ``slash_and_unicode_example``
    and ``empty_fields_example`` for worked input/canonical-bytes/digest
    triples Billing's implementer can assert against directly.
    """
    text = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def compute_record_context_digest(data: Any) -> str:
    """``"sha256:" + hex`` of ``canonical_json_bytes(data)`` — the exact value the
    JWT ``record_context_digest`` claim must equal for a given ``record_context``
    body value. ``data`` is the raw (not-yet-validated) JSON value at the wire's
    ``record_context`` key — digesting the raw value, not a Pydantic-normalized
    one, means a shape-malformed body still gets a well-defined digest, so the
    401 binding check and the 422 shape check are independent layers (task-A3-brief.md:
    "a request with invalid context is never processed as if the context were
    absent")."""
    return "sha256:" + hashlib.sha256(canonical_json_bytes(data)).hexdigest()


class RecordContextRecord(BaseModel):
    """One trusted record row inside a ``record_context`` body."""

    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: ResourceType
    record_id: _NonEmptyRecordId
    label: str = Field(min_length=1, max_length=MAX_LABEL_LEN)
    fields: dict[str, str] = Field(default_factory=dict)
    link_key: str | None = Field(default=None, min_length=1)

    @field_validator("fields")
    @classmethod
    def _bounded_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_FIELDS_PER_RECORD:
            raise ValueError(f"fields exceeds maximum of {MAX_FIELDS_PER_RECORD}")
        for key, val in value.items():
            if not key or len(key) > MAX_FIELD_KEY_LEN:
                raise ValueError("invalid field key")
            if len(val) > MAX_FIELD_VALUE_LEN:
                raise ValueError(f"field {key!r} exceeds max length")
        return value


class RecordContext(BaseModel):
    """Strictly validated v1 ``record_context`` request-body field."""

    model_config = ConfigDict(strict=True, extra="forbid")

    version: Literal[1]
    title: str = Field(min_length=1, max_length=MAX_TITLE_LEN)
    records: list[RecordContextRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _bounded_total_bytes(cls, data: Any) -> Any:
        """Total-bytes cap, checked first (before any per-field validation) and
        against the exact same canonical recipe the digest uses."""
        if isinstance(data, dict) and len(canonical_json_bytes(data)) > MAX_RECORD_CONTEXT_BYTES:
            raise ValueError(f"record_context exceeds maximum of {MAX_RECORD_CONTEXT_BYTES} bytes")
        return data

    @field_validator("version", mode="before")
    @classmethod
    def _version_is_a_plain_int(cls, value: object) -> object:
        # Literal[1] alone accepts 1.0 (float) via loose "==" equality even
        # under strict=True — bool/float must be rejected before that check
        # runs, or "version": 1.0 would silently pass (A2's schema_version lesson).
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("version must be exactly 1 (int)")
        return value

    @field_validator("records")
    @classmethod
    def _bounded_records(cls, value: list[RecordContextRecord]) -> list[RecordContextRecord]:
        if len(value) > MAX_RECORDS:
            raise ValueError(f"records exceeds maximum of {MAX_RECORDS}")
        return value
