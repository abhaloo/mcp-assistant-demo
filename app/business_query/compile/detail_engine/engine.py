"""DetailEngine implementation for resolving and reading signed detail facts."""

from __future__ import annotations

import re
import time
from collections import OrderedDict, defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from app.auth import Principal
from app.business_query.authorize.capability import detail_permissions_satisfied
from app.business_query.compile.detail_engine.projection import (
    _LOCAL_DETAIL_SOURCE_COLUMNS,
    _MAX_PARENT_IDS,
    CanonicalDetailSelection,
    DetailReadResult,
    DetailSelectionRefused,
    reflect_source_table,
    registered_source_columns,
    validate_selection,
)
from app.business_query.compile.detail_engine.rows import (
    build_record_detail,
)
from app.business_query.definitions import DefinitionBundle
from app.business_query.outcomes import RecordDetail
from app.business_query.plan import BusinessQueryPlan, DetailSelection
from app.business_query.plan.detail_family import FamilyAliasIndex
from app.telemetry.spans import detail_stage_span

_ALL_VISIBLE_DETAILS_RE = re.compile(
    r"\b(?:all|every)\s+(?:the\s+)?(?:principal[- ]visible\s+|visible\s+)"
    r"(?:typed\s+)?details?\b",
    re.IGNORECASE,
)


def expand_all_visible_detail_selections(
    question: str,
    plan: BusinessQueryPlan,
    *,
    principal: Principal,
    bundle: DefinitionBundle,
) -> BusinessQueryPlan:
    """Expand an explicit all-visible-details request from the signed catalog."""
    if _ALL_VISIBLE_DETAILS_RE.search(question) is None:
        return plan

    selections: list[DetailSelection] = []
    for family in sorted({detail.family_key for detail in bundle.detail_definitions}):
        revisions = [
            detail
            for detail in bundle.detail_definitions
            if detail.family_key == family and detail.is_current
        ]
        if len(revisions) != 1:
            return plan
        if detail_permissions_satisfied(revisions[0], principal):
            selections.append(DetailSelection(family=family, revision_mode="recorded"))
    if not selections:
        return plan
    return plan.model_copy(update={"detail_selections": selections})


def align_detail_selections_to_explicit_question(
    question: str,
    plan: BusinessQueryPlan,
    *,
    bundle: DefinitionBundle,
) -> BusinessQueryPlan:
    """Use exact signed family phrases to remove planner-added detail families."""
    mentioned = FamilyAliasIndex(bundle.detail_definitions).families_mentioned_in(question)
    if not mentioned:
        return plan
    existing = {selection.family: selection for selection in plan.detail_selections}
    selections = [
        existing.get(family, DetailSelection(family=family, revision_mode="recorded"))
        for family in sorted(mentioned)
    ]
    return plan.model_copy(update={"detail_selections": selections})


class DetailEngine:
    """Resolve and read detail families from signed source metadata only."""

    def __init__(
        self,
        engine: Engine,
        bundle: DefinitionBundle | None = None,
        principal: Principal | None = None,
        *,
        source_contracts: dict[str, frozenset[str]] | None = None,
        sql_recorder: Any | None = None,
        ledger_scope: str | None = None,
    ) -> None:
        self._engine = engine
        self._bundle = bundle
        self._principal = principal
        self._metadata = sa.MetaData()
        self._tables: dict[str, sa.Table] = {}
        self._source_contracts = dict(_LOCAL_DETAIL_SOURCE_COLUMNS)
        self._sql_recorder = sql_recorder
        self._ledger_scope = ledger_scope
        if source_contracts:
            self._source_contracts.update(source_contracts)

    def resolve(
        self,
        selections: Sequence[DetailSelection],
        *,
        principal: Principal,
        bundle: DefinitionBundle,
    ) -> tuple[CanonicalDetailSelection, ...]:
        """Authorize each selection and bind it to one signed source."""
        canonical: list[CanonicalDetailSelection] = []
        for selection in selections:
            validate_selection(selection)
            definitions = [
                detail
                for detail in bundle.detail_definitions
                if detail.family_key == selection.family
            ]
            if not definitions:
                raise DetailSelectionRefused("unknown_detail")
            if selection.revision_hash is not None:
                definitions = [
                    detail
                    for detail in definitions
                    if detail.revision_hash == selection.revision_hash
                ]
            elif selection.revision_mode == "current":
                definitions = [detail for detail in definitions if detail.is_current]
            if not definitions:
                raise DetailSelectionRefused("stale_revision")
            if selection.revision_mode == "current" and len(definitions) != 1:
                raise DetailSelectionRefused("ambiguous_detail_revision")
            if selection.revision_mode == "exact" and len(definitions) != 1:
                raise DetailSelectionRefused("ambiguous_detail_revision")
            if any(not detail_permissions_satisfied(detail, principal) for detail in definitions):
                raise DetailSelectionRefused("unauthorized_detail")
            source = next(
                (item for item in bundle.detail_sources if item.family_key == selection.family),
                None,
            )
            if source is None:
                raise DetailSelectionRefused("detail_source_unavailable")
            registered_source_columns(source, self._source_contracts)
            if any(
                source.projection_view != detail.physical_source
                or source.owner_resource != detail.owner_resource
                or source.owner_column != detail.owner_column
                or source.typed_value_column != detail.value_column
                or source.scope_columns != detail.scope_columns
                for detail in definitions
            ):
                raise DetailSelectionRefused("detail_source_mismatch")
            definition = min(definitions, key=lambda item: item.revision_hash)
            canonical.append(
                CanonicalDetailSelection(
                    selection=selection,
                    definition=definition,
                    definitions=tuple(definitions),
                    source=source,
                )
            )
        return tuple(canonical)

    def read(
        self,
        owner_refs: Sequence[int | str],
        selections: Sequence[CanonicalDetailSelection],
        *,
        principal: Principal,
        entity_id: int | str | None,
        department_id: int | str | None = None,
        department_required: bool = False,
        trace: object | None = None,
    ) -> DetailReadResult:
        """Read authorized facts with owner and principal scope predicates."""
        parent_ids = list(OrderedDict.fromkeys(owner_refs))[:_MAX_PARENT_IDS]
        if not parent_ids or not selections:
            return DetailReadResult(details=())
        by_family: dict[str, list[CanonicalDetailSelection]] = defaultdict(list)
        for selection in selections:
            by_family[selection.selection.family].append(selection)

        details: list[RecordDetail] = []
        failed_families: list[str] = []
        for family in sorted(by_family):
            selection = by_family[family][0]
            source = selection.source
            table = reflect_source_table(
                self._engine,
                self._metadata,
                self._tables,
                source,
                self._source_contracts,
                record_sql_fn=self._record_sql,
                trace=trace,
            )
            owner_column = table.c[source.owner_column]
            typed_column = table.c[source.typed_value_column]
            display_column = table.c[source.display_value_column]
            family_column = table.c.get("family_key")
            if family_column is None:
                raise DetailSelectionRefused("detail_source_mismatch")
            revision_column = table.c.get("revision_hash")
            columns = [owner_column, typed_column, display_column]
            if revision_column is not None:
                columns.append(revision_column)
            optional_columns = {
                name: table.c.get(name)
                for name in (
                    "revision",
                    "attribute_key",
                    "validation_state",
                    "source",
                    "provenance",
                    "source_fingerprint",
                    "source_updated_at",
                    "profile_revision_hash",
                    "unit",
                )
            }
            columns.extend(column for column in optional_columns.values() if column is not None)
            stmt = sa.select(*columns).where(
                owner_column.in_(parent_ids),
                family_column == family,
            )
            scope_columns = source.scope_columns
            if scope_columns.entity is not None:
                if entity_id is None or (
                    principal.entity_id is None and not principal.cross_entity
                ):
                    raise DetailSelectionRefused("missing_entity_scope")
                if (
                    principal.entity_id is not None
                    and not principal.cross_entity
                    and str(entity_id) != str(principal.entity_id)
                ):
                    raise DetailSelectionRefused("entity_scope_mismatch")
                entity_column = table.c.get(scope_columns.entity)
                if entity_column is None:
                    raise DetailSelectionRefused("detail_source_mismatch")
                stmt = stmt.where(entity_column == entity_id)
            if scope_columns.department is not None:
                if department_required and department_id is None:
                    raise DetailSelectionRefused("missing_department_scope")
                department_column = table.c.get(scope_columns.department)
                if department_column is None:
                    raise DetailSelectionRefused("detail_source_mismatch")
                if department_id is not None:
                    stmt = stmt.where(department_column == department_id)
            if revision_column is not None and (
                selection.selection.revision_hash is not None
                or selection.selection.revision_mode == "current"
            ):
                target_revision = (
                    selection.selection.revision_hash or selection.definition.revision_hash
                )
                stmt = stmt.where(revision_column == target_revision)
            order_columns = [owner_column]
            source_updated_column = optional_columns["source_updated_at"]
            if selection.selection.revision_mode == "as_of":
                if source_updated_column is None or selection.selection.as_of is None:
                    raise DetailSelectionRefused("detail_source_mismatch")
                stmt = stmt.where(source_updated_column <= selection.selection.as_of)
                order_columns.append(source_updated_column.desc())
                observation_revision = optional_columns["revision"]
                if observation_revision is not None:
                    order_columns.append(observation_revision.desc())
                if revision_column is not None:
                    order_columns.append(revision_column)
            else:
                if revision_column is not None:
                    order_columns.append(revision_column)
                if source_updated_column is not None:
                    order_columns.append(source_updated_column)
            stmt = stmt.order_by(*order_columns)
            started = time.perf_counter()
            try:
                with self._engine.connect() as connection:
                    rows = connection.execute(stmt).mappings().fetchall()
            except sa.exc.SQLAlchemyError:
                self._record_sql(stmt, started, None, trace)
                failed_families.append(family)
                continue
            self._record_sql(stmt, started, len(rows), trace)
            seen_as_of_observations: set[tuple[Any, Any]] = set()
            for row in rows:
                if selection.selection.revision_mode == "as_of":
                    observation_key = (
                        row[source.owner_column],
                        (
                            row.get("attribute_key")
                            if selection.definition.cardinality == "one_to_many"
                            and optional_columns["attribute_key"] is not None
                            else None
                        ),
                    )
                    if observation_key in seen_as_of_observations:
                        continue
                    seen_as_of_observations.add(observation_key)
                detail = build_record_detail(
                    row,
                    source,
                    selection,
                    family=family,
                    revision_column=revision_column,
                    optional_columns=optional_columns,
                )
                details.append(detail)
        return DetailReadResult(
            details=tuple(details),
            failed_families=tuple(sorted(set(failed_families))),
        )

    def read_record_details_result(
        self,
        entity_id: int | str,
        parent_ids: Sequence[int | str],
        family_ids: Sequence[str] | Sequence[DetailSelection],
        *,
        principal: Principal | None = None,
        bundle: DefinitionBundle | None = None,
        department_id: int | str | None = None,
        department_required: bool = False,
        trace: object | None = None,
    ) -> DetailReadResult:
        """Resolve and read details, retaining safe per-family source failures."""
        selected = [
            item if isinstance(item, DetailSelection) else DetailSelection(family=item)
            for item in family_ids
        ]
        selected_bundle = bundle or self._bundle
        selected_principal = principal or self._principal
        if selected_bundle is None or selected_principal is None:
            raise DetailSelectionRefused("missing_detail_contract")
        canonical = self.resolve(selected, principal=selected_principal, bundle=selected_bundle)
        return self.read(
            parent_ids,
            canonical,
            principal=selected_principal,
            entity_id=entity_id,
            department_id=department_id,
            department_required=department_required,
            trace=trace,
        )

    def read_record_details(
        self,
        entity_id: int | str,
        parent_ids: Sequence[int | str],
        family_ids: Sequence[str] | Sequence[DetailSelection],
        *,
        principal: Principal | None = None,
        bundle: DefinitionBundle | None = None,
        department_id: int | str | None = None,
        department_required: bool = False,
        trace: object | None = None,
    ) -> list[RecordDetail]:
        with detail_stage_span() as span:
            try:
                result = self._read_record_details_impl(
                    entity_id,
                    parent_ids,
                    family_ids,
                    principal=principal,
                    bundle=bundle,
                    department_id=department_id,
                    department_required=department_required,
                    trace=trace,
                )
            except Exception as exc:
                span.record_exception(exc)
                span.set_attribute("bq.outcome", "error")
                raise
            span.set_attribute("bq.outcome", "completed")
            return result

    def _read_record_details_impl(
        self,
        entity_id: int | str,
        parent_ids: Sequence[int | str],
        family_ids: Sequence[str] | Sequence[DetailSelection],
        *,
        principal: Principal | None = None,
        bundle: DefinitionBundle | None = None,
        department_id: int | str | None = None,
        department_required: bool = False,
        trace: object | None = None,
    ) -> list[RecordDetail]:
        """Resolve and read authorized detail facts for the supplied owners."""
        selected = [
            item if isinstance(item, DetailSelection) else DetailSelection(family=item)
            for item in family_ids
        ]
        selected_bundle = bundle or self._bundle
        selected_principal = principal or self._principal
        if selected_bundle is None or selected_principal is None:
            raise DetailSelectionRefused("missing_detail_contract")
        result = self.read_record_details_result(
            entity_id,
            parent_ids,
            selected,
            principal=selected_principal,
            bundle=selected_bundle,
            department_id=department_id,
            department_required=department_required,
            trace=trace,
        )
        if result.failed_families:
            raise DetailSelectionRefused("detail_source_unavailable")
        return list(result.details)

    def read_record_details_mapping(
        self,
        entity_id: int | str,
        parent_ids: Sequence[int | str],
        family_ids: Sequence[str] | Sequence[DetailSelection],
        *,
        principal: Principal | None = None,
        bundle: DefinitionBundle | None = None,
        department_id: int | str | None = None,
        department_required: bool = False,
        trace: object | None = None,
    ) -> dict[Any, list[RecordDetail]]:
        mapping: dict[Any, list[RecordDetail]] = defaultdict(list)
        for detail in self.read_record_details(
            entity_id,
            parent_ids,
            family_ids,
            principal=principal,
            bundle=bundle,
            department_id=department_id,
            department_required=department_required,
            trace=trace,
        ):
            mapping[detail.owner_id].append(detail)
        return dict(mapping)

    read_job_details = read_record_details
    read_job_details_result = read_record_details_result
    read_job_details_mapping = read_record_details_mapping

    def _record_sql(
        self,
        statement: sa.sql.ClauseElement,
        started: float,
        rows: int | None,
        trace: object | None,
    ) -> None:
        """Record detail metadata/read SQL through the shared SQL ledger."""
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        try:
            rendered = str(
                statement.compile(
                    dialect=self._engine.dialect,
                    compile_kwargs={"literal_binds": True},
                )
            )
        except Exception:
            rendered = str(statement.compile(dialect=self._engine.dialect))
        if self._sql_recorder is not None:
            self._sql_recorder(
                statement=rendered,
                elapsed_ms=elapsed_ms,
                row_count=rows,
                receipt_query_id=getattr(trace, "answer_query_id", None),
                scope=self._ledger_scope,
                correlation_id=getattr(trace, "correlation_id", None),
            )
