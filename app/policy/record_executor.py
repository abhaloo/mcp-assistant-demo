"""
Policy-scoped record executor.

Mirrors ``app/rag/sql_access.py``'s gating seam exactly: a config setting
alone (``MCP_RECORD_DATABASE_URL``) can never make the record tools
reachable.

    ``ensure_record_tools_available(principal)`` raises unless BOTH:
      1. ``settings.mcp_record_database_url is not None``, AND
      2. ``principal`` carries a validated v2 ``record_access`` snapshot
         (``entity_id``/``manifest_hash``/``resources`` all populated — see
         ``app/auth/record_access.py``). A v1/claimless principal never
         satisfies this, regardless of role or permissions.

No fallback to ``settings.mcp_billing_database_url`` EVER, in either
direction — this module never references that setting at all (grep it: the
only database-URL setting this file reads is ``mcp_record_database_url``).

``PolicyScopedRecordExecutor`` is constructed per request (never cached
across requests: it closes over one principal's identity and one engine).
Construction re-runs ``ensure_record_tools_available`` (last-mile check) and
then verifies ``principal.manifest_hash`` is a MEMBER of the bounded accepted
set (``app.policy.manifest_loader.accepted_manifest_hashes()`` — at most two:
current + previous) — a hash outside that set fails closed
(``RecordAccessDenied``), never falls back to trusting the claim or "whatever
is newest" alone. This activates the v2.1 identity contract: a token minted
against a manifest older than N-1, or one that was never vendored at all,
cannot query records under an artifact it never agreed to. The
executor then loads and queries against the EXACT bundle the principal's
hash names — never the current bundle by default — so an in-flight N-1 token
surviving an ordinary rolling deploy still gets the schema it was minted
against, not today's.

Every query this class builds is parameterized SQLAlchemy Core
(``select()``/``and_()``/``.where(col == :bind)``/``.like(:bind)``) built ONLY
from ``ManifestResource`` definitions (``app/policy/manifest_loader.py``) plus
the principal's own snapshot values — model/tool-caller text supplies VALUES
only (bound parameters), never table/column names, operators, or which
resource/entity/department a query targets. See ``_resource_and_table`` for
the authorization gate (resource/action/field_set — forbidden == missing) and
``_scope_predicates`` for why entity/department binding is DECLARATION-driven
(``resource.scope_columns.entity``/``.department``, vendored from Billing's
own manifest declarations) rather than derived from which columns a
generated table happens to have, or from a hand-mapped ``entity_scope`` enum
(see its docstring for the concrete gap declaration-driven binding closes:
``credit_note``'s ``via_parent`` scope declares a parent-derived
``entity_id`` column and is entity-scoped-queryable, not merely "whatever
columns the loader happened to generate").
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.engine import Engine

from app.auth import Principal
from app.config import settings
from app.policy.manifest_loader import (
    Manifest,
    ManifestResource,
    accepted_manifest_hashes,
    build_metadata,
    load_manifest,
)
from app.policy.record_query import (
    CanonicalQueryError,
    FilterClause,
    SortClause,
    normalize_business_datetime,
    validate_canonical_filter_clauses,
)
from app.resources import ProcessResources

_DENY_MESSAGE = "business record tools are currently unavailable"

# The least-privilege default when a caller doesn't specify which
# field_set it wants — every one of today's 10 real manifest resources
# declares "summary" (see app/policy/manifest/policy-manifest.json), and a
# caller that DOES need "detail" must ask for it explicitly.
_DEFAULT_FIELD_SET = "summary"

# Defense-in-depth ceiling independent of whatever a tool's own Pydantic
# input model enforces (app/policy/record_tools.py) — protects any future
# caller that invokes the executor directly, bypassing the typed tool layer.
_MAX_ROWS = 50

_TYPE_CHECKS = {
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "datetime": lambda v: isinstance(v, str),
}

_FILTER_OPERATORS = {
    "eq": lambda column, value: column == value,
    "gt": lambda column, value: column > value,
    "gte": lambda column, value: column >= value,
    "lt": lambda column, value: column < value,
    "lte": lambda column, value: column <= value,
}

# .like(f"%{query}%") binds the VALUE as a parameter (no SQL injection), but
# does nothing about LIKE's own metacharacters — an unescaped "%"/"_" inside
# a user-supplied query string still acts as a SQL wildcard, letting a
# caller broaden a match within their already-authorized row set (e.g. "_"
# alone matches any non-empty field, not literal underscore).
# _escape_like_value + the explicit ESCAPE clause below make search() match
# "%"/"_" literally, like every other character.
_LIKE_ESCAPE_CHAR = "\\"


def _escape_like_value(value: str) -> str:
    """Escape LIKE metacharacters (and the escape character itself) so a
    search query matches its literal text, never a broader wildcard pattern.
    The escape character must be escaped FIRST — escaping "%"/"_" before it
    would double-escape any literal backslash already inserted for them."""
    escaped = value.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
    escaped = escaped.replace("%", _LIKE_ESCAPE_CHAR + "%")
    escaped = escaped.replace("_", _LIKE_ESCAPE_CHAR + "_")
    return escaped


class RecordAccessDenied(Exception):
    """Raised when a caller may not use the business-record tools right now.

    The message is deliberately generic (mirrors
    ``app.eval.sql.agent.access.StructuredAccessDenied``) — it must never name the
    config key, the mismatched hash, the missing resource/action, or any
    other internal reason, so nothing here can leak containment state to a
    caller once this is ever wired to a client-facing path.
    """


def ensure_record_tools_available(principal: Principal | None) -> None:
    """Raise ``RecordAccessDenied`` unless the record tools are reachable for
    this principal right now. See the module docstring for the two
    independent conditions required."""
    if settings.mcp_record_database_url is None:
        raise RecordAccessDenied(_DENY_MESSAGE)
    if (
        principal is None
        or principal.manifest_hash is None
        or principal.entity_id is None
        or principal.resources is None
    ):
        raise RecordAccessDenied(_DENY_MESSAGE)


class RecordRow(BaseModel):
    """One returned row's typed identity + fields, captured BEFORE any LLM
    formatting — the provenance seed the reference ledger consumes. This
    model does not build that ledger, only supplies its typed inputs."""

    model_config = ConfigDict(strict=True, extra="forbid")

    resource_type: str
    record_id: str
    fields: dict[str, Any]


class RecordGroup(BaseModel):
    """One bounded aggregate bucket.  It has no record identity and must
    never be turned into a record link by a later presenter."""

    model_config = ConfigDict(strict=True, extra="forbid")

    label: str
    count: int


class PolicyScopedRecordExecutor:
    """Request-scoped executor bound to one principal + one engine.

    Construct via ``build_record_executor(principal, resources=...)`` in production (it
    supplies the engine only after the gate passes); tests may construct
    directly with a fixture engine.
    """

    def __init__(self, principal: Principal, engine: Engine) -> None:
        ensure_record_tools_available(principal)  # last-mile re-check
        # Membership in the bounded ACCEPTED set (current + previous — at
        # most two), not equality against a single hash. This is what lets
        # an in-flight token minted against yesterday's manifest keep
        # working through an ordinary rolling deploy/rollback; a hash
        # outside the accepted set (unknown, stale-beyond-N-1, or forged)
        # still fails closed.
        if principal.manifest_hash not in accepted_manifest_hashes():
            raise RecordAccessDenied(_DENY_MESSAGE)
        self._principal = principal
        self._engine = engine
        # Load the EXACT bundle this principal's token was minted against —
        # never "whichever is newest" — and build this request's SQLAlchemy
        # metadata from THAT bundle specifically, so a previous-manifest
        # principal queries against the schema it actually agreed to, not
        # today's current one.
        self._manifest: Manifest = load_manifest(principal.manifest_hash)
        self._metadata = build_metadata(self._manifest)

    # -- read-only vocabulary introspection ---------------------------------

    def resource_vocabulary(self, resource_type: str) -> ManifestResource | None:
        """Read-only manifest EXISTENCE lookup against THIS executor's own
        bound manifest (never the current/latest one -- this executor may
        be running against a previous-accepted bundle, see the class
        docstring above). Mirrors
        ``app/services/record_intent.py``'s ``validate_intent_vocabulary``
        (manifest vocabulary only, NEVER authorization -- whether a
        declared name is actually GRANTED to this principal stays this
        class's exclusive authority via ``_resource_and_table``/
        ``_bind_user_filters``, untouched by this accessor).

        This method exposes manifest existence for callers that need a
        non-authorizing vocabulary lookup. It makes no authorization decision
        and gates nothing on its own. A caller that skips the executor based
        on this result still relies on the manifest-existence fact
        ``_resource_and_table``/``_bind_user_filters`` enforce."""
        return self._manifest.resources.get(resource_type)

    def capability_card(self) -> str:
        """Render the current principal's declared record vocabulary.

        This is a request-local prompt aid, not an authorization decision:
        every call still reaches the normal grant and scope checks below.
        Values, ids, and scope identifiers are intentionally omitted.
        """
        entries: list[str] = []
        for resource_type in sorted(self._principal.resources or {}):
            resource = self._manifest.resources.get(resource_type)
            grant = (self._principal.resources or {}).get(resource_type)
            if resource is None or grant is None:
                continue
            operations: list[str] = []
            if "search" in resource.operations and "search" in grant.actions:
                operations.append("search")
            if "list" in resource.operations and "read" in grant.actions:
                operations.extend(
                    operation for operation in ("list", "get") if operation in resource.operations
                )
            if settings.record_analytics_mode in {"count", "grouped"} and "read" in grant.actions:
                if "count" in resource.operations:
                    operations.append("count")
            if settings.record_analytics_mode == "grouped" and "read" in grant.actions:
                operations.extend(
                    operation
                    for operation in ("group_count", "top_groups")
                    if operation in resource.operations
                )
            filters = (
                ", ".join(
                    f"{spec.field} ({'/'.join(self._enabled_filter_operators(spec.operators))})"
                    for spec in resource.filters
                    if spec.field in self._user_filter_fields(resource)
                    and self._enabled_filter_operators(spec.operators)
                )
                or "none"
            )
            sorts = (
                ", ".join(f"{spec.field} ({'/'.join(spec.directions)})" for spec in resource.sorts)
                or "none"
            )
            groups = (
                ", ".join(
                    field
                    for field in resource.groupable_fields
                    if field in resource.readable_fields
                )
                if settings.record_analytics_mode == "grouped"
                else "none"
            ) or "none"
            entries.append(
                f"- {resource_type}: operations={','.join(operations) or 'none'}; "
                f"filters={filters}; sorts={sorts}; groups={groups}"
            )
        return "Structured-record capability card:\n" + "\n".join(entries or ["- none"])

    @staticmethod
    def _enabled_filter_operators(operators: list[str]) -> list[str]:
        if settings.record_filter_schema_v2:
            return operators
        return [operator for operator in operators if operator == "eq"]

    # -- authorization -----------------------------------------------------

    def _resource_and_table(
        self,
        resource_type: str,
        *,
        action: str,
        field_set: str = _DEFAULT_FIELD_SET,
        operation: str | None = None,
    ):
        """The authorization gate checks the SPECIFIC requested
        ``field_set`` (not merely "any overlap exists" between the
        resource's and the grant's field_sets) — ``field_set`` must be both
        a set the manifest declares for this resource AND one the
        principal's snapshot actually grants. Requesting an ungranted or
        unrecognized set stays denied with the same generic message
        (forbidden == missing)."""
        resource = self._manifest.resources.get(resource_type)
        if resource is None:
            # Absent from the manifest entirely — nothing to query.
            raise RecordAccessDenied(_DENY_MESSAGE)
        if operation is not None and operation not in resource.operations:
            raise RecordAccessDenied(_DENY_MESSAGE)
        grants = self._principal.resources or {}
        grant = grants.get(resource_type)
        if grant is None or action not in grant.actions:
            # Not in the snapshot, or granted without the action this tool
            # needs — forbidden == missing.
            raise RecordAccessDenied(_DENY_MESSAGE)
        if field_set not in resource.field_sets or field_set not in grant.field_sets:
            # The manifest doesn't declare this set for the resource, or the
            # principal's snapshot doesn't grant it — still forbidden == missing.
            raise RecordAccessDenied(_DENY_MESSAGE)
        table = self._metadata.tables[resource.projection_name]
        return resource, table

    # -- predicate building --------------------------------------------------

    def _scope_predicates(
        self,
        resource: ManifestResource,
        table,
    ) -> list[Any]:
        """Entity/department/subtype predicates that apply to EVERY query for
        this resource, regardless of caller input.

        Entity/department binding is DECLARATION-driven — ``resource.
        scope_columns.entity``/``.department`` names the column to bind,
        never derived from which columns a generated table happens to have.
        A column-name-presence check alone could not distinguish "this
        resource has no scope column" from "this resource happens to have a
        readable field that's also named entity_id" — the manifest says so
        explicitly instead of RAG inferring it.

        ``credit_note`` (``entity_scope="via_parent"``) declares
        ``scope_columns.entity == "entity_id"`` — a parent-derived column
        Billing's view flattens from the ``bill`` join — so it binds like
        any other resource for non-cross-entity principals. No declared
        entity column + non-cross-entity principal is still an immediate
        denial before any statement is built — see
        ``TestViaParentScope::test_any_resource_without_a_declared_entity_column_denies_for_a_normal_principal``,
        which constructs a resource with ``scope_columns.entity=None``
        rather than one that merely omits the column from its virtual
        table. ``cross_entity=True`` principals are exempt entirely (no
        column needed — they are authorized to see every entity).
        """
        predicates: list[Any] = []
        if not self._principal.cross_entity:
            entity_column = resource.scope_columns.entity
            if entity_column is None:
                raise RecordAccessDenied(_DENY_MESSAGE)
            predicates.append(table.c[entity_column] == self._principal.entity_id)
        department_column = resource.scope_columns.department
        if department_column is not None:
            scope_values = self._principal.scope_values
            department_id = scope_values.department_id if scope_values else None
            department_scope_mode = resource.department_scope_mode
            if department_scope_mode is None:
                raise RecordAccessDenied(_DENY_MESSAGE)
            if department_scope_mode == "none":
                department_id = None
            elif department_id is None and department_scope_mode == "required_match":
                raise RecordAccessDenied(_DENY_MESSAGE)
            elif department_id is not None:
                predicates.append(table.c[department_column] == department_id)
        for field, value in resource.record_predicates.items():
            predicates.append(table.c[field] == value)
        return predicates

    def _user_filter_fields(self, resource: ManifestResource) -> dict[str, Any]:
        """Manifest filter fields a tool caller may bind a VALUE to. Excludes
        the DECLARED scope columns (``scope_columns.entity``/``.department``,
        always forced from the principal, never user-supplied) and any column
        the manifest pins via ``record_predicates`` (always forced to that
        exact value — a "subtype" predicate, never user-settable)."""
        reserved = {
            column
            for column in (resource.scope_columns.entity, resource.scope_columns.department)
            if column is not None
        }
        reserved |= set(resource.record_predicates.keys())
        return {spec.field: spec for spec in resource.filters if spec.field not in reserved}

    def _canonical_filter_clauses(self, filters: list[FilterClause] | None) -> list[FilterClause]:
        """Accept only explicit typed clauses at the authorization boundary."""
        if filters is None:
            return []
        if not isinstance(filters, list):
            raise RecordAccessDenied(_DENY_MESSAGE)
        return filters

    def _bound_filter_value(self, declared_type: str, value: Any) -> Any:
        if not _TYPE_CHECKS[declared_type](value):
            raise RecordAccessDenied(_DENY_MESSAGE)
        if declared_type != "datetime":
            return value
        try:
            return normalize_business_datetime(value, self._manifest.business_timezone)
        except CanonicalQueryError as error:
            raise RecordAccessDenied(_DENY_MESSAGE) from error

    def _bind_user_filters(
        self, resource: ManifestResource, table, filters: list[FilterClause] | None
    ) -> list[Any]:
        allowed = self._user_filter_fields(resource)
        predicates: list[Any] = []
        clauses = self._canonical_filter_clauses(filters)
        datetime_fields = {spec.field for spec in allowed.values() if spec.type == "datetime"}
        try:
            clauses = validate_canonical_filter_clauses(
                clauses, self._manifest.business_timezone, datetime_fields
            )
        except CanonicalQueryError as error:
            raise RecordAccessDenied(_DENY_MESSAGE) from error
        for clause in clauses:
            spec = allowed.get(clause.field)
            if spec is None:
                # Not a manifest-declared, user-exposable filter field for
                # this resource — deny rather than silently ignore or coerce.
                raise RecordAccessDenied(_DENY_MESSAGE)
            if clause.operator not in spec.operators:
                raise RecordAccessDenied(_DENY_MESSAGE)
            if clause.operator != "eq" and not settings.record_filter_schema_v2:
                raise RecordAccessDenied(_DENY_MESSAGE)
            value = self._bound_filter_value(spec.type, clause.value)
            predicates.append(_FILTER_OPERATORS[clause.operator](table.c[clause.field], value))
        return predicates

    def _searchable_fields(self, resource: ManifestResource) -> list[str]:
        """Free-text search targets: every displayed field except the
        canonical identifier (matching an id via substring text makes no
        sense for a free-text query). Deliberately distinct from
        ``_user_filter_fields`` — ``filters`` describes exact-match WHERE
        columns (mostly id/entity_id/department_id/subtype discriminators in
        this manifest snapshot), while ``readable_fields`` are the columns a
        record actually displays (description, name, invoice_number, ...) —
        the fields a human free-text query is actually about."""
        return [f for f in resource.readable_fields if f != resource.canonical_link_key]

    # -- public API ----------------------------------------------------------

    def search(
        self,
        resource_type: str,
        query: str,
        filters: list[FilterClause] | None = None,
        *,
        field_set: str = _DEFAULT_FIELD_SET,
        limit: int = 10,
    ) -> list[RecordRow]:
        resource, table = self._resource_and_table(
            resource_type, action="search", field_set=field_set
        )
        predicates = self._scope_predicates(resource, table)
        predicates += self._bind_user_filters(resource, table, filters or [])
        searchable = self._searchable_fields(resource)
        if query:
            if not searchable:
                return []
            escaped_query = _escape_like_value(query)
            predicates.append(
                or_(
                    *(
                        table.c[f].like(f"%{escaped_query}%", escape=_LIKE_ESCAPE_CHAR)
                        for f in searchable
                    )
                )
            )
        stmt = self._select(resource, table, predicates, field_set=field_set, limit=limit)
        return self._execute(stmt, resource_type, resource)

    def list(
        self,
        resource_type: str,
        filters: list[FilterClause] | None = None,
        *,
        sort: SortClause | str | None = None,
        field_set: str = _DEFAULT_FIELD_SET,
        limit: int = 20,
    ) -> list[RecordRow]:
        resource, table = self._resource_and_table(
            resource_type, action="read", field_set=field_set
        )
        predicates = self._scope_predicates(resource, table)
        predicates += self._bind_user_filters(resource, table, filters or [])
        stmt = self._select(resource, table, predicates, field_set=field_set, limit=limit)
        if sort is not None:
            try:
                if isinstance(sort, str):
                    sort_clause = SortClause(field=sort, direction="asc")
                else:
                    sort_clause = sort
            except Exception as error:
                raise RecordAccessDenied(_DENY_MESSAGE) from error
            if not isinstance(sort_clause, SortClause):
                raise RecordAccessDenied(_DENY_MESSAGE)
            allowed_sorts = {spec.field: spec for spec in resource.sorts}
            spec = allowed_sorts.get(sort_clause.field)
            if spec is None or sort_clause.direction not in spec.directions:
                raise RecordAccessDenied(_DENY_MESSAGE)
            ordering = (
                desc(table.c[sort_clause.field])
                if sort_clause.direction == "desc"
                else asc(table.c[sort_clause.field])
            )
            tie_break = (
                desc(table.c[resource.canonical_link_key])
                if sort_clause.direction == "desc"
                else asc(table.c[resource.canonical_link_key])
            )
            stmt = stmt.order_by(ordering, tie_break)
        return self._execute(stmt, resource_type, resource)

    def get(
        self, resource_type: str, record_ids: list[int], *, field_set: str = _DEFAULT_FIELD_SET
    ) -> list[RecordRow]:
        resource, table = self._resource_and_table(
            resource_type, action="read", field_set=field_set
        )
        predicates = self._scope_predicates(resource, table)
        link_column = resource.canonical_link_key
        predicates.append(table.c[link_column].in_(record_ids))
        stmt = self._select(
            resource, table, predicates, field_set=field_set, limit=len(record_ids) or 1
        )
        return self._execute(stmt, resource_type, resource)

    def count(
        self,
        resource_type: str,
        filters: list[FilterClause] | None = None,
        *,
        field_set: str = _DEFAULT_FIELD_SET,
    ) -> int:
        """Return one scoped count when the dedicated count canary is on."""
        if settings.record_analytics_mode not in {"count", "grouped"}:
            raise RecordAccessDenied(_DENY_MESSAGE)
        resource, table = self._resource_and_table(
            resource_type,
            action="read",
            field_set=field_set,
            operation="count",
        )
        predicates = self._scope_predicates(resource, table)
        predicates += self._bind_user_filters(resource, table, filters)
        stmt = select(func.count()).select_from(table)
        if predicates:
            stmt = stmt.where(and_(*predicates))
        with self._engine.connect() as conn:
            return int(conn.execute(stmt).scalar_one())

    def group_count(
        self,
        resource_type: str,
        group_by: str,
        filters: list[FilterClause] | None = None,
        *,
        field_set: str = _DEFAULT_FIELD_SET,
        limit: int = 20,
        top_groups: bool = False,
    ) -> list[RecordGroup]:
        """Return at most twenty scope-filtered groups.

        ``top_groups`` is a separate manifest operation so a producer can
        expose stable grouped counts without necessarily authorizing ranking.
        """
        if settings.record_analytics_mode != "grouped":
            raise RecordAccessDenied(_DENY_MESSAGE)
        operation = "top_groups" if top_groups else "group_count"
        resource, table = self._resource_and_table(
            resource_type,
            action="read",
            field_set=field_set,
            operation=operation,
        )
        if (
            group_by not in resource.groupable_fields
            or group_by not in resource.readable_fields
            or group_by not in resource.field_set_fields[field_set]
        ):
            raise RecordAccessDenied(_DENY_MESSAGE)
        predicates = self._scope_predicates(resource, table)
        predicates += self._bind_user_filters(resource, table, filters)
        group_column = table.c[group_by]
        group_total = func.count().label("group_total")
        stmt = select(group_column.label("group_value"), group_total).group_by(group_column)
        if predicates:
            stmt = stmt.where(and_(*predicates))
        bounded_limit = max(1, min(limit, 20))
        if top_groups:
            stmt = stmt.order_by(desc(group_total), asc(group_column))
        else:
            stmt = stmt.order_by(asc(group_column))
        stmt = stmt.limit(bounded_limit)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [
            RecordGroup(
                label="Unassigned" if row["group_value"] is None else str(row["group_value"])[:120],
                count=int(row["group_total"]),
            )
            for row in rows
        ]

    def top_groups(
        self,
        resource_type: str,
        group_by: str,
        filters: list[FilterClause] | None = None,
        *,
        field_set: str = _DEFAULT_FIELD_SET,
        limit: int = 10,
    ) -> list[RecordGroup]:
        return self.group_count(
            resource_type,
            group_by,
            filters,
            field_set=field_set,
            limit=limit,
            top_groups=True,
        )

    # -- internals -------------------------------------------------------

    def _select(
        self,
        resource: ManifestResource,
        table,
        predicates: list[Any],
        *,
        field_set: str,
        limit: int,
    ):
        """Projects EXACTLY ``resource.field_set_fields[field_set]`` — the
        granted set's declared fields, never the resource's full
        ``readable_fields`` — so a ``summary``-only grant can never see
        ``detail``-only fields, and a declared scope column (never a member
        of any field_set — see manifest_loader's
        ``_scope_columns_never_projected_in_a_field_set`` validator) can
        never leak into tool output. ``field_set`` is already verified
        against both the manifest and the grant by ``_resource_and_table``
        before this is ever called."""
        columns = [table.c[field] for field in resource.field_set_fields[field_set]]
        bounded_limit = max(1, min(limit, _MAX_ROWS))
        stmt = select(*columns).limit(bounded_limit)
        if predicates:
            # and_() with zero args is deprecated (SQLAlchemy 2.0) — some
            # resources (e.g. "credit_note", via_parent) have neither a scope
            # column nor a record_predicates entry, so predicates can be [].
            stmt = stmt.where(and_(*predicates))
        return stmt

    def _execute(self, stmt, resource_type: str, resource: ManifestResource) -> list[RecordRow]:
        link_column = resource.canonical_link_key
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [
            RecordRow(
                resource_type=resource_type,
                record_id=str(row[link_column]),
                fields=dict(row),
            )
            for row in rows
        ]


def build_record_executor(
    principal: Principal | None, *, resources: ProcessResources
) -> PolicyScopedRecordExecutor:
    """Production factory: the gate runs BEFORE any engine is resolved, so
    ``MCP_RECORD_DATABASE_URL is None`` (the default) guarantees zero
    connection construction, not just zero queries.

    The container is the parameter, never a resolved ``Engine``: an ``Engine``
    argument would be evaluated by the caller before this function runs, which
    would build a pool ahead of the gate.
    """
    ensure_record_tools_available(principal)
    assert principal is not None  # narrows for type checkers; already proven above
    return PolicyScopedRecordExecutor(principal=principal, engine=resources.policy_record_engine)
