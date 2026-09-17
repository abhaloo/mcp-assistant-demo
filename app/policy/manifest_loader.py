"""
Vendored policy-manifest loader.

``app/policy/manifest/`` is a vendored, code-versioned mirror of Billing's
committed manifest artifact(s) — the source of truth for which business-record
resources exist, which columns they expose, and which row predicates/entity
scope apply to each. This module parses the artifact(s) and computes their
content hash so ``PolicyScopedRecordExecutor``
(``app/policy/record_executor.py``) can verify a principal's ``manifest_hash``
claim against them.

On-disk layout::

    app/policy/manifest/
      index.json              # {schema_version, current, accepted: [<=2 hashes]}
      bundles/
        <hash-hex>.json        # one immutable manifest per accepted hash

A rolling deployment or rollback authenticates a JWT against whichever
manifest hash was CURRENT at mint time. Denying every hash except today's
current one would disable structured record access for every in-flight
token during an ordinary deploy. ``index.json`` names at most two accepted
hashes (current + previous); each is immutable, independently hash-verified,
and independently loadable — the executor loads the exact bundle the
principal's token was minted against, never "whichever is newest".

Hash recipe — reused, not reinvented: each bundle's content hash is
``compute_record_context_digest()`` from ``app/models/record_context.py``
(the SAME ``sort_keys``/no-whitespace/``ensure_ascii=False``/``allow_nan=False``
canonical-JSON recipe built for the ``record_context_digest`` claim).
Billing computes its manifest hash with the identical recipe, so this is one
canonical recipe shared by both the ``record_context`` digest and every
manifest bundle's hash, not a second one that could drift from it.
``index.json``'s ``current``/``accepted`` entries and each bundle file's OWN
filename must all agree with this recomputed hash — ``InvalidManifestIndexError``
fails closed on any disagreement, a missing bundle file, or a malformed index
(more than two accepted hashes, ``current`` not a member of ``accepted``,
duplicate entries, a badly-shaped hash string) — see
``load_manifest_index()``/``load_manifest()``/``verify_manifest_startup()``
below.

``build_metadata()`` derives a SQLAlchemy ``Table`` per resource — name and
columns generated FROM the manifest artifact (``projection_name`` for the
table name; the union of every field referenced anywhere in that resource's
``filters``, ``sorts``, ``readable_fields``, and ``record_predicates`` keys
for columns) — never hand-authored. ``PolicyScopedRecordExecutor`` and the
test suite's SQLite fixture DB both call this SAME function, so a manifest
edit that adds/renames/removes a field changes the query-building schema and
the test fixture schema identically — manifest drift breaks tests loudly
instead of silently diverging.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table

from app.models.record_context import compute_record_context_digest

_MANIFEST_DIR = Path(__file__).parent / "manifest"
_INDEX_PATH = _MANIFEST_DIR / "index.json"
_BUNDLES_DIR = _MANIFEST_DIR / "bundles"

# At most two accepted hashes (current + previous) at any time.
_MAX_ACCEPTED_BUNDLES = 2

_MANIFEST_HASH_PATTERN = r"^sha256:[0-9a-fA-F]{64}$"

# Load-time guard: every readable_fields/field_set_fields/record_predicates/
# filters/sorts/scope_columns entry binds verbatim as a SQL column name
# (`table.c[field]`) in PolicyScopedRecordExecutor — a dot, backtick, or
# whitespace in any of those (e.g. a relation-path label like
# "customer.name") never becomes a real, bindable SQLAlchemy column. This is
# a single, reusable ASCII-identifier allowlist, never a denylist of "bad"
# characters — the same fail-closed-by-default posture the rest of this
# module uses.
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InvalidManifestFieldNameError(ValueError):
    """Raised (wrapped into a Pydantic ``ValidationError``) when a manifest
    field name is not a valid single SQL identifier, so a caller can
    recognize this specific failure class rather than a generic
    ``ValueError``."""


def _assert_valid_sql_identifier(name: str, *, context: str) -> None:
    if not _SQL_IDENTIFIER_RE.fullmatch(name):
        raise InvalidManifestFieldNameError(
            f"{context} {name!r} is not a valid single SQL identifier — manifest field "
            "names must contain only letters, digits, and underscores, and must not "
            "start with a digit. Dots, backticks, and whitespace (e.g. a relation-path "
            "label like 'customer.name') are never valid here — the exported column "
            "name is required instead."
        )


FilterType = Literal["int", "string", "datetime"]
FilterOperator = Literal["eq", "gt", "gte", "lt", "lte"]
SortDirection = Literal["asc", "desc"]
DepartmentScopeMode = Literal["none", "filter_when_present", "required_match"]
RecordOperation = Literal["search", "list", "get", "count", "group_count", "top_groups"]

_SQL_TYPE_FOR: dict[FilterType, type] = {
    "int": Integer,
    "string": String,
    "datetime": DateTime,
}


class ManifestFilterSpec(BaseModel):
    """One ``{field, type}`` entry from a resource's ``filters`` list.

    A filter entry carries ``operators``, never ``directions`` (a sort-only
    concept) — kept as a separate model from ``ManifestSortSpec`` so
    ``strict=True, extra="forbid"`` actually enforces that split instead of
    accepting either shape."""

    model_config = ConfigDict(strict=True, extra="forbid")

    field: str
    operators: list[FilterOperator] = Field(default_factory=lambda: ["eq"])
    type: FilterType


class ManifestSortSpec(BaseModel):
    """One ``{field, type}`` entry from a resource's ``sorts`` list. See
    ``ManifestFilterSpec`` for why this is a separate class rather than one
    shared spec."""

    model_config = ConfigDict(strict=True, extra="forbid")

    field: str
    directions: list[SortDirection] = Field(default_factory=lambda: ["asc"])
    type: FilterType


class ScopeColumns(BaseModel):
    """Per-resource declared scope-binding column names.
    ``PolicyScopedRecordExecutor._scope_predicates`` binds
    ``entity``/``department`` against the principal's
    ``entity_id``/``scope_values.department_id`` for THIS resource —
    declaration-driven, not derived from which columns a generated table
    happens to have. ``credit_note``'s ``entity`` is parent-derived
    (Billing's view flattens the ``bill`` join into a plain ``entity_id``
    column) but is still just a column name here — RAG's loader/executor
    never need to know it came from a join.

    ``None`` means "this resource has no bindable column for this scope" —
    the executor must fail closed for a principal that needs it, never
    execute unscoped (see record_executor.py's module docstring).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    entity: str | None
    department: str | None

    @field_validator("entity", "department")
    @classmethod
    def _non_blank_when_declared(cls, value: str | None) -> str | None:
        """A blank/whitespace-only column name can never become a real
        SQLAlchemy ``Column`` (``Column("", Integer())`` raises
        ``sqlalchemy.exc.ArgumentError`` deep inside ``Table`` construction).
        Rejecting it HERE, at strict Pydantic parse time, turns that into a
        clean, controlled ``ValidationError`` (matching the loader's
        fail-closed posture) instead of an uncontrolled internal SQLAlchemy
        exception surfacing out of ``build_metadata()`` later — no new error
        oracle."""
        if value is not None and not value.strip():
            raise ValueError("scope column name must not be blank")
        return value


class ManifestResource(BaseModel):
    """One resource entry under the manifest's top-level ``resources`` key.

    Field names/shape match Billing's artifact verbatim — see
    ``app/policy/manifest/policy-manifest.json``. ``model_class``,
    ``*_permission*`` (Spatie permission strings) belong to Billing's own
    token-minting policy and are parsed here for completeness/drift-detection
    only; RAG's authorization decision reads the v2 ``record_access`` snapshot
    (``principal.resources``), never these Laravel permission strings — see
    ``app/policy/record_executor.py`` for why.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    canonical_link_key: str
    department_scope_mode: DepartmentScopeMode | None = None
    entity_scope: Literal["standard", "department", "via_parent"]
    field_set_fields: dict[str, list[str]]
    field_sets: list[str]
    filters: list[ManifestFilterSpec]
    groupable_fields: list[str] = Field(default_factory=list)
    link_permission_mode: Literal["any", "all"]
    link_permissions: list[str]
    model_class: str
    operations: list[RecordOperation] = Field(default_factory=lambda: ["search", "list", "get"])
    parent_relation: str | None
    projection_name: str
    read_permission_mode: Literal["any", "all"]
    read_permissions: list[str]
    readable_fields: list[str]
    record_predicates: dict[str, str]
    scope_columns: ScopeColumns
    search_permission: str
    sorts: list[ManifestSortSpec]

    @model_validator(mode="after")
    def _department_scope_mode_has_a_declared_column(self) -> ManifestResource:
        if (
            self.department_scope_mode in {"filter_when_present", "required_match"}
            and self.scope_columns.department is None
        ):
            raise ValueError("department scope mode requires a declared department column")
        return self

    @model_validator(mode="after")
    def _canonical_link_key_is_selectable(self) -> ManifestResource:
        """``PolicyScopedRecordExecutor._execute`` always SELECTs exactly
        ``readable_fields`` and then reads ``row[canonical_link_key]`` to
        build each ``RecordRow.record_id`` — if the artifact ever declared a
        ``canonical_link_key`` that isn't also in ``readable_fields``, that
        would be a ``KeyError`` deep inside query execution instead of a
        clear failure here, at manifest-load time."""
        if self.canonical_link_key not in self.readable_fields:
            raise ValueError(
                f"canonical_link_key {self.canonical_link_key!r} must be "
                "included in readable_fields"
            )
        return self

    @model_validator(mode="after")
    def _field_set_fields_keys_match_field_sets(self) -> ManifestResource:
        """``field_set_fields`` is the per-set field projection Billing's
        ``PolicyManifestValidator`` guarantees for every resource in the
        real artifact — RAG re-verifies the same shape invariant rather
        than trusting it blindly. A mismatch here means the manifest
        declares a ``field_sets`` entry with no corresponding field list
        (or vice versa) — ``get_business_records(field_set=...)`` would
        have no fields to project for that set."""
        declared = set(self.field_set_fields.keys())
        expected = set(self.field_sets)
        if declared != expected:
            raise ValueError(
                f"field_set_fields keys {sorted(declared)} must exactly match "
                f"field_sets {sorted(expected)}"
            )
        return self

    @model_validator(mode="after")
    def _field_set_fields_are_readable_and_selectable(self) -> ManifestResource:
        """Every ``field_set_fields[set]`` member must (a) be a real,
        displayed field of this resource (``⊆ readable_fields``, mirroring
        Billing's own ``assertFieldSetFieldsAreReadable``) and (b) include
        ``canonical_link_key`` — the field-set-projected SELECT
        (``record_executor.py``'s ``_select``) is the only select statement,
        so if a set's field list omitted the link key, ``RecordRow.record_id``
        could never be built for a row returned under that set."""
        readable = set(self.readable_fields)
        for field_set, fields in self.field_set_fields.items():
            unreadable = [f for f in fields if f not in readable]
            if unreadable:
                raise ValueError(
                    f"field_set_fields[{field_set!r}] contains fields outside "
                    f"readable_fields: {unreadable}"
                )
            if self.canonical_link_key not in fields:
                raise ValueError(
                    f"field_set_fields[{field_set!r}] must include "
                    f"canonical_link_key {self.canonical_link_key!r}"
                )
        return self

    @model_validator(mode="after")
    def _groupable_fields_are_readable(self) -> ManifestResource:
        unreadable = [field for field in self.groupable_fields if field not in self.readable_fields]
        if unreadable:
            raise ValueError(f"groupable_fields contains unreadable fields: {unreadable}")
        return self

    @model_validator(mode="after")
    def _scope_columns_never_projected_in_a_field_set(self) -> ManifestResource:
        """Scope columns exist ONLY for predicate binding — a resource that
        (incorrectly) declared its entity/department column as also being
        one of a field set's displayed fields would leak it straight into
        tool output. Caught here, at load time, rather than trusted to
        never happen at query time."""
        scope_names = {c for c in (self.scope_columns.entity, self.scope_columns.department) if c}
        if not scope_names:
            return self
        for field_set, fields in self.field_set_fields.items():
            leaked = scope_names & set(fields)
            if leaked:
                raise ValueError(
                    f"field_set_fields[{field_set!r}] must never include scope "
                    f"columns: {sorted(leaked)}"
                )
        return self

    @model_validator(mode="after")
    def _declared_scope_columns_are_not_impossibly_typed(self) -> ManifestResource:
        """Defensive validation: if a declared scope column ALSO appears in
        this resource's own ``filters``/``sorts`` with an explicit,
        non-``"int"`` type, the declaration is self-contradictory — every
        entity/department scope predicate binds a Python ``int``
        (``principal.entity_id`` / ``scope_values.department_id``), so a
        column independently declared ``"string"``/``"datetime"`` could
        never correctly satisfy it. This is Billing's
        ``assertScopeColumnsProjected`` counterpart on RAG's side: RAG has
        no access to Billing's live view DDL, so it re-verifies internal
        consistency instead — a declared scope column that would not exist
        as a coherent integer-typed column on the generated table is
        manifest-invalid, fail closed."""
        declared_types: dict[str, FilterType] = {}
        for spec in (*self.filters, *self.sorts):
            declared_types.setdefault(spec.field, spec.type)
        for label, column in (
            ("entity", self.scope_columns.entity),
            ("department", self.scope_columns.department),
        ):
            if column is None:
                continue
            conflicting_type = declared_types.get(column)
            if conflicting_type is not None and conflicting_type != "int":
                raise ValueError(
                    f"scope_columns.{label}={column!r} conflicts with a "
                    f"{conflicting_type!r}-typed filter/sort field of the same "
                    "name — scope columns must resolve to an integer column"
                )
        return self

    @model_validator(mode="after")
    def _every_field_name_is_a_valid_sql_identifier(self) -> ManifestResource:
        """Every name ``PolicyScopedRecordExecutor`` could ever bind as a
        SQL column (``readable_fields``, ``field_set_fields`` members,
        ``record_predicates`` keys, ``filters``/``sorts`` field names,
        ``scope_columns`` values, and ``canonical_link_key``) must be a
        real, single SQL identifier. Checked HERE, at manifest-load time,
        so a bad manifest fails loudly and closed before any query is ever
        built, rather than surfacing as a live "Unknown column" SQL error
        the first time the field is actually queried."""
        _assert_valid_sql_identifier(self.canonical_link_key, context="canonical_link_key")
        for field in self.readable_fields:
            _assert_valid_sql_identifier(field, context="readable_fields member")
        for field_set, fields in self.field_set_fields.items():
            for field in fields:
                _assert_valid_sql_identifier(
                    field, context=f"field_set_fields[{field_set!r}] member"
                )
        for column in self.record_predicates:
            _assert_valid_sql_identifier(column, context="record_predicates key")
        for spec in (*self.filters, *self.sorts):
            _assert_valid_sql_identifier(spec.field, context="filters/sorts field")
        for label, column in (
            ("entity", self.scope_columns.entity),
            ("department", self.scope_columns.department),
        ):
            if column is not None:
                _assert_valid_sql_identifier(column, context=f"scope_columns.{label}")
        return self


class Manifest(BaseModel):
    """One vendored policy-manifest BUNDLE: resource name -> definition."""

    model_config = ConfigDict(strict=True, extra="forbid")

    business_timezone: Literal["Africa/Dar_es_Salaam"] | None = None
    resources: dict[str, ManifestResource]


class InvalidManifestIndexError(ValueError):
    """Raised (fail closed) when ``app/policy/manifest/index.json`` is
    malformed, an accepted hash names a bundle file that does not exist, or a
    bundle's recomputed content hash disagrees with its filename/index entry.
    Distinct from ``pydantic.ValidationError`` (raised for ``index.json``'s
    own SHAPE — schema_version, hash-string well-formedness, accepted
    cardinality) because this class covers cross-checks that need real file
    I/O, not pure field validation — callers that need to catch "the on-disk
    manifest set is untrustworthy" as one thing should catch
    ``(pydantic.ValidationError, InvalidManifestIndexError)``."""


class ManifestIndex(BaseModel):
    """Strictly validated ``index.json`` shape::

        {"schema_version": 1, "current": "sha256:<hex>", "accepted": ["sha256:<hex>", ...]}

    Validates ONLY the index's own shape — hash-string well-formedness,
    ``accepted`` bounded to at most two entries (current + previous),
    ``current`` must be a member of ``accepted``, no duplicate entries. Does
    NOT touch bundle files on disk (see ``load_manifest()``/
    ``verify_manifest_startup()`` for the filename/content-hash agreement
    check, which is per-bundle I/O and belongs at the loader-function level,
    not the parse-shape level)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: Literal[1]
    current: Annotated[str, Field(pattern=_MANIFEST_HASH_PATTERN)]
    accepted: list[Annotated[str, Field(pattern=_MANIFEST_HASH_PATTERN)]]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _schema_version_is_a_plain_int(cls, value: object) -> object:
        # Literal[1] alone accepts 1.0 (float) via loose "==" equality even
        # under strict=True.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("schema_version must be exactly 1 (int)")
        return value

    @model_validator(mode="after")
    def _accepted_is_bounded_deduped_and_contains_current(self) -> ManifestIndex:
        """``accepted`` contains at most two hashes (current + previous) —
        the previous slot fills when a manifest-changing release vendors a
        new current. More than two accepted hashes is a malformed index and
        must fail startup, not merely "extra compatibility"."""
        if not self.accepted:
            raise ValueError("accepted must name at least one manifest hash")
        if len(self.accepted) > _MAX_ACCEPTED_BUNDLES:
            raise ValueError(
                f"accepted must name at most {_MAX_ACCEPTED_BUNDLES} manifest hashes "
                f"(current + previous), got {len(self.accepted)}"
            )
        if len(set(self.accepted)) != len(self.accepted):
            raise ValueError("accepted must not contain duplicate hashes")
        if self.current not in self.accepted:
            raise ValueError("current must be a member of accepted")
        return self


def _bundle_path(bundles_dir: Path, manifest_hash: str) -> Path:
    """The on-disk path a given ``"sha256:<hex>"`` string's bundle file MUST
    live at — the hex digest alone (no ``sha256:`` prefix — a literal ``:``
    is not a legal Windows filename character) plus ``.json``."""
    hex_digest = manifest_hash.removeprefix("sha256:")
    return bundles_dir / f"{hex_digest}.json"


def _read_and_verify_bundle(bundles_dir: Path, manifest_hash: str) -> Manifest:
    """Pure, uncached bundle load: read ``bundles_dir/<hex>.json``, recompute
    its canonical-JSON content hash, and require that computed hash to equal
    ``manifest_hash`` EXACTLY (the filename/index entry this bundle is
    claimed under) before ever strictly parsing it into a ``Manifest`` — a
    bundle file that was renamed, corrupted, or paired with the wrong hash
    fails closed here, never silently loads under the wrong identity. The
    identifier guard (``ManifestResource``'s
    ``_every_field_name_is_a_valid_sql_identifier`` validator) runs on every
    call via ``Manifest.model_validate()`` — this is the ONE place any
    bundle, current or previous, is ever parsed, so the guard applies
    uniformly rather than only to whichever bundle happens to be
    "current"."""
    path = _bundle_path(bundles_dir, manifest_hash)
    if not path.is_file():
        raise InvalidManifestIndexError(
            f"accepted manifest hash {manifest_hash!r} names a bundle file "
            f"that does not exist: {path}"
        )
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    computed_hash = compute_record_context_digest(raw)
    if computed_hash != manifest_hash:
        raise InvalidManifestIndexError(
            f"bundle {path.name!r} content hash {computed_hash!r} does not match "
            f"its accepted index/filename hash {manifest_hash!r} — tampered or "
            "mismatched bundle file"
        )
    return Manifest.model_validate(raw)


@lru_cache(maxsize=1)
def load_manifest_index() -> ManifestIndex:
    """Strictly parsed + shape-validated ``index.json``. Cached — the
    artifact is code-versioned and immutable for the lifetime of the
    process (mirrors the old single-manifest loader's caching story)."""
    with _INDEX_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return ManifestIndex.model_validate(raw)


def accepted_manifest_hashes() -> tuple[str, ...]:
    """Every ``"sha256:<hex>"`` string a principal's ``manifest_hash`` claim
    may equal right now (at most two: current + previous) —
    ``PolicyScopedRecordExecutor`` checks membership in this, not equality
    against a single value: it executes the exact authenticated bundle if
    it is in the bounded accepted set."""
    return tuple(load_manifest_index().accepted)


@lru_cache(maxsize=_MAX_ACCEPTED_BUNDLES)
def _cached_manifest(manifest_hash: str) -> Manifest:
    """The ONE per-hash bundle cache, bounded to
    ``_MAX_ACCEPTED_BUNDLES`` (2) entries — a hash outside
    ``accepted_manifest_hashes()`` is rejected BEFORE any file I/O or
    caching happens, so this can never grow past the accepted set's own
    cardinality regardless of how many distinct (accepted or not) hashes
    ``load_manifest()`` is ever called with. ``functools.lru_cache`` does not
    cache exceptions, so a denied/unknown hash re-raises fresh on every call
    (fail closed every time, not just the first)."""
    if manifest_hash not in accepted_manifest_hashes():
        raise InvalidManifestIndexError(f"{manifest_hash!r} is not an accepted manifest hash")
    return _read_and_verify_bundle(_BUNDLES_DIR, manifest_hash)


def load_manifest(manifest_hash: str | None = None) -> Manifest:
    """Strictly parsed, hash-verified manifest bundle.

    ``manifest_hash=None`` (the default) returns the CURRENT bundle — the
    sensible "just give me the manifest" story every hash-agnostic caller
    (``build_metadata()`` fixtures, ``app/policy/__init__.py``, ...) already
    relies on. Pass an explicit ``"sha256:<hex>"`` (as
    ``PolicyScopedRecordExecutor`` does, keyed off the principal's own
    ``manifest_hash`` claim) to load that SPECIFIC accepted bundle instead —
    raises ``InvalidManifestIndexError`` if it is not currently accepted."""
    target = manifest_hash if manifest_hash is not None else load_manifest_index().current
    return _cached_manifest(target)


def manifest_content_hash() -> str:
    """The CURRENT bundle's verified ``"sha256:" + hex`` hash — what a v2
    JWT is minted against right now. Existing hash-agnostic callers (tests
    that just need "a hash that matches the real manifest",
    ``tests/contracts.py``'s ``manifest_principal()``) keep working
    unchanged; callers that need to accept an N-1 rollback window use
    ``accepted_manifest_hashes()`` instead."""
    return load_manifest_index().current


def verify_manifest_startup() -> ManifestIndex:
    """Eager, fail-closed verification for process startup (``app.main``'s
    lifespan, before the app accepts traffic) and for the ongoing readiness
    check (``app.health.checks``'s manifest-index check): loads
    ``index.json`` (shape-validates it) and then loads EVERY accepted
    bundle — not just current — which transitively re-verifies each one's
    file presence, filename/content/index hash agreement, and strict,
    identifier-guarded ``Manifest`` parse (``_cached_manifest``/
    ``_read_and_verify_bundle`` above are the ONE place that logic lives,
    so startup verification and "the loader itself" verification can never
    drift apart). A malformed index or a missing/tampered bundle raises here
    immediately, rather than lazily surfacing on the first record-tool call
    a rolled-out-but-broken deploy happens to receive."""
    index = load_manifest_index()
    for accepted_hash in index.accepted:
        load_manifest(accepted_hash)
    return index


def _raw_manifest_dict() -> dict:
    """The CURRENT bundle's raw parsed JSON — the exact pre-image the content
    hash is computed over (not a Pydantic-normalized dump; see the module
    docstring's hash-recipe note). Back-compat helper used by
    ``tests/policy/test_manifest_loader.py`` to build mutated copies of the
    real artifact for negative-path tests."""
    path = _bundle_path(_BUNDLES_DIR, load_manifest_index().current)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _generated_field_names(resource: ManifestResource) -> list[str]:
    """Every field name this resource's virtual table will have as a column,
    in declaration order, deduplicated. Declared scope columns
    (``scope_columns.entity``/``.department``) are ALWAYS included, even for
    a resource that never references them anywhere else in its manifest
    entry — ``credit_note``'s parent-derived ``entity_id`` is the concrete
    case: Billing's view projects it, but ``credit_note`` names it nowhere
    in ``filters``/``sorts``/``readable_fields``/``record_predicates``.
    ``PolicyScopedRecordExecutor`` needs a real column to bind its entity/
    department predicate against regardless of whether that column is ever
    displayed."""
    field_order: list[str] = []
    for name in (
        *(spec.field for spec in resource.filters),
        *(spec.field for spec in resource.sorts),
        *(
            column
            for column in (resource.scope_columns.entity, resource.scope_columns.department)
            if column is not None
        ),
        *resource.readable_fields,
        *resource.record_predicates.keys(),
    ):
        if name not in field_order:
            field_order.append(name)
    return field_order


def _columns_for(resource: ManifestResource) -> list[Column]:
    """Every column this resource's virtual table needs, generated from the
    manifest artifact: filter/sort fields get their declared SQL type;
    declared scope columns not already typed by a filter/sort default to
    ``Integer`` (entity/department scope columns are always integer ids);
    any other field referenced only in ``readable_fields`` or as a
    ``record_predicates`` key (and not already covered above) defaults to
    ``String`` — the manifest declares no type for those, and every such
    field this artifact actually uses is textual."""
    declared_types: dict[str, FilterType] = {}
    for spec in (*resource.filters, *resource.sorts):
        declared_types.setdefault(spec.field, spec.type)
    for column in (resource.scope_columns.entity, resource.scope_columns.department):
        if column is not None:
            declared_types.setdefault(column, "int")

    return [
        Column(name, _SQL_TYPE_FOR[declared_types.get(name, "string")]())
        for name in _generated_field_names(resource)
    ]


def build_metadata(manifest: Manifest) -> MetaData:
    """One ``MetaData`` describing every resource's projection view as a
    SQLAlchemy ``Table`` — table name = ``projection_name``, columns
    generated by ``_columns_for`` — entirely FROM the manifest artifact, never
    hand-written. Both ``PolicyScopedRecordExecutor`` (query building) and the
    test suite (SQLite fixture DDL via ``metadata.create_all()``) call this
    same function, so they can never silently drift apart."""
    metadata = MetaData()
    for resource in manifest.resources.values():
        Table(resource.projection_name, metadata, *_columns_for(resource))
    return metadata
