<?php

declare(strict_types=1);

namespace App\Authorization;

use InvalidArgumentException;

/**
 * Task B6 / R1 — the manifest-declaration validation rules
 * PolicyManifestExporter::export() runs for every resource type, FAILING
 * THE EXPORT (never silently emitting a bad manifest) on any violation:
 *
 *  1. a declared scope column (entity or department) is not a column the
 *     resource's ai_v1_* projection view actually SELECTs (cross-checked
 *     against Projections\ProjectionDefinitions, the same class B5's own
 *     ProjectionViewColumnsTest proves matches the LIVE database view);
 *  2. a field_set_fields member is not in PolicyManifestExporter's
 *     readable_fields for that resource type;
 *  3. `scopeEntityColumn === null` without the explicit registry-level
 *     `scopeEntityColumnNullJustified` flag;
 *  4. (Task B8 / R3 — the real gap a live e2e run caught, 2026-07-24: rule 2
 *     above only ever compared field_set_fields against readable_fields —
 *     BOTH still carrying dotted relation-path labels like `customer.name`
 *     at that point in export(). Neither list was EVER cross-checked
 *     against the FLATTENED column names (`customer_name`) the resource's
 *     real ai_v1_* view actually SELECTs, so a dotted field_set_fields
 *     member sailed through both existing checks and only blew up when
 *     RAG's PolicyScopedRecordExecutor tried to bind it as a literal SQL
 *     column — `assertFieldSetFieldsAreProjected()` closes exactly that
 *     gap, called with the ALREADY-flattened field_set_fields (see
 *     PolicyManifestExporter::export()) against the same projected-columns
 *     list rule 1 already trusts.)
 *  5. (Task B1b / F4) a resource that declares a department scope column
 *     but no explicit `department_scope_mode` (the constructor default is
 *     now null/undeclared, never a silent 'none') — `department_scope_mode`
 *     itself, once declared, must also be one of the three known values;
 *  6. an unknown operator on a typed filter, or an unknown direction on a
 *     sort-direction override (Task B1b / R1 — RAG's `FilterOperator`/
 *     `SortDirection` vocabularies);
 *  7. a `groupable_fields` member that is not one of this resource's own
 *     (flattened) readable fields (Task B1b / R1, mirrors RAG's own
 *     `_groupable_fields_are_readable` load-time rule);
 *  8. (Task B1b fix-round-1 / I1) `group_count`/`top_groups` declared with no
 *     groupable_fields at all — an operation with no authorized GROUP BY
 *     dimension;
 *  9. (Task B1b fix-round-1 / I2) a typedFilters field name that is not a
 *     column the resource's ai_v1_* projection view actually SELECTs — the
 *     same class of gap rule 4 closed for field_set_fields, now closed for
 *     typedFilters too.
 *
 * Each method takes a BusinessRecordDefinition plus whatever external
 * context it checks against, as plain data — no registry, no DB, no
 * PolicyManifestExporter dependency of its own, so this class (and its
 * tests) stay exactly as framework-light as the rest of the Authorization
 * namespace. PolicyManifestExporter is the ONLY caller.
 */
final class PolicyManifestValidator
{
    private function __construct() {}

    public static function assertOperationsAreSupported(BusinessRecordDefinition $definition): void
    {
        foreach ($definition->operations as $operation) {
            if (! in_array($operation, [
                'search',
                'list',
                'get',
                'count',
                'group_count',
                'top_groups',
            ], true)) {
                throw new InvalidArgumentException(
                    "Resource [{$definition->resourceType}] declares an unknown structured record operation ".
                    "[{$operation}]."
                );
            }
        }
    }

    /**
     * Task B1b / F4 — null (undeclared) is a legal, deliberate value now:
     * assertDepartmentScopeModeIsDeclaredWhenColumnPresent() is the guard
     * that rejects null when it is NOT legal (a declared department scope
     * column with no explicit mode). This method only ever needs to check a
     * NON-null value against the three-member vocabulary.
     */
    public static function assertDepartmentScopeModeIsSupported(BusinessRecordDefinition $definition): void
    {
        if ($definition->departmentScopeMode === null) {
            return;
        }

        if (! in_array($definition->departmentScopeMode, [
            BusinessRecordDefinition::DEPARTMENT_SCOPE_NONE,
            BusinessRecordDefinition::DEPARTMENT_SCOPE_FILTER_WHEN_PRESENT,
            BusinessRecordDefinition::DEPARTMENT_SCOPE_REQUIRED_MATCH,
        ], true)) {
            throw new InvalidArgumentException(
                "Resource [{$definition->resourceType}] declares an unknown department scope mode ".
                "[{$definition->departmentScopeMode}]."
            );
        }
    }

    /**
     * Task B1b / F4 (2026-07-27 adversarial review) — the blocker the
     * finding exists to prevent: BusinessRecordDefinition's constructor used
     * to default $departmentScopeMode to 'none', turning "nobody declared a
     * mode for this department-scoped resource" into "explicitly unfiltered"
     * — a silent permissive default the plan forbids. Now that the
     * constructor default is null (undeclared), THIS guard is what makes a
     * declared $scopeDepartmentColumn require an explicit mode: 'none' +
     * a declared column stays legal (plan §3.2 — an explicit choice, not a
     * default); only the silent, undeclared combination fails. A resource
     * with no department column may leave the mode null (the exporter
     * resolves that to 'none' itself — there is nothing to scope).
     */
    public static function assertDepartmentScopeModeIsDeclaredWhenColumnPresent(BusinessRecordDefinition $definition): void
    {
        if ($definition->scopeDepartmentColumn !== null && $definition->departmentScopeMode === null) {
            throw new InvalidArgumentException(
                "Resource [{$definition->resourceType}] declares a department scope column ".
                "[{$definition->scopeDepartmentColumn}] without an explicit department_scope_mode."
            );
        }
    }

    /**
     * Task B1b / R1 — every operator a $definition->typedFilters entry
     * declares must be one of the five RAG's ManifestFieldSpec parses
     * (app/policy/manifest_loader.py's FilterOperator Literal): eq/gt/gte/
     * lt/lte. Mirrors assertOperationsAreSupported()'s shape for a different
     * vocabulary.
     */
    public static function assertTypedFilterOperatorsAreSupported(BusinessRecordDefinition $definition): void
    {
        foreach ($definition->typedFilters as $field => $spec) {
            foreach ($spec['operators'] as $operator) {
                if (! in_array($operator, ['eq', 'gt', 'gte', 'lt', 'lte'], true)) {
                    throw new InvalidArgumentException(
                        "Resource [{$definition->resourceType}]'s typed filter [{$field}] declares an unknown ".
                        "operator [{$operator}]."
                    );
                }
            }
        }
    }

    /**
     * Task B1b / R1 — every direction a $definition->sortDirectionOverrides
     * entry declares must be one of RAG's two SortDirection values: asc/desc.
     */
    public static function assertSortDirectionsAreSupported(BusinessRecordDefinition $definition): void
    {
        foreach ($definition->sortDirectionOverrides as $field => $directions) {
            foreach ($directions as $direction) {
                if (! in_array($direction, ['asc', 'desc'], true)) {
                    throw new InvalidArgumentException(
                        "Resource [{$definition->resourceType}]'s sort [{$field}] declares an unknown direction ".
                        "[{$direction}]."
                    );
                }
            }
        }
    }

    /**
     * Task B1b / R1 — mirrors RAG's own `_groupable_fields_are_readable`
     * manifest-load-time rule (app/policy/manifest_loader.py): a field RAG's
     * group_count/top_groups may group by must already be one of this
     * resource's readable fields — a hidden scope column or an unreadable
     * field could otherwise leak into aggregate output labels.
     *
     * @param  list<string>  $readableFields  The FLATTENED readable_fields this manifest entry
     *                                        actually emits (PolicyManifestExporter::export()'s
     *                                        $flattenedReadableFields) — the same wire-level list
     *                                        RAG's own validator checks membership against.
     */
    public static function assertGroupableFieldsAreReadable(
        BusinessRecordDefinition $definition,
        array $readableFields,
    ): void {
        foreach ($definition->groupableFields as $field) {
            if (! in_array($field, $readableFields, true)) {
                throw new InvalidArgumentException(
                    "Resource [{$definition->resourceType}]'s groupable field [{$field}] is not in readable_fields."
                );
            }
        }
    }

    /**
     * Task B1b fix-round-1 / I1 (task review, 2026-07-27) — declaring
     * group_count or top_groups without any groupable_fields is either dead
     * on arrival for RAG's future group operations (no allowlist to
     * constrain GROUP BY) or, worse, an invitation to group by whatever
     * dimension a future executor implementation happens to pick, since the
     * manifest never authorized one. A resource that declares either
     * grouping operation must also declare at least one groupable field.
     * (Job currently declares neither operation — see
     * BusinessRecordRegistry's own comment on why they were removed pending
     * task B2's non-empty groupable_fields.)
     */
    public static function assertGroupOperationsHaveGroupableFields(BusinessRecordDefinition $definition): void
    {
        $declaresGroupOperation = array_intersect(['group_count', 'top_groups'], $definition->operations) !== [];

        if ($declaresGroupOperation && $definition->groupableFields === []) {
            throw new InvalidArgumentException(
                "Resource [{$definition->resourceType}] declares a group operation (group_count/top_groups) ".
                'without any groupable_fields.'
            );
        }
    }

    /**
     * Task B1b fix-round-1 / I2 (task review, 2026-07-27) — the 2026-07-24 B8
     * bug class, replayed for typedFilters: `filterFieldNames()` (the base
     * filter set) is cross-checked against the resource's projected view
     * columns via ProjectionDefinitions::columnsFor() (which reads
     * filterFieldNames() into its own union), but typedFilters is a SEPARATE
     * declaration ProjectionDefinitions knows nothing about — nothing
     * previously stopped a typed filter from naming a column the view does
     * not actually SELECT. That field feeds directly into filtersFor()'s
     * exported `filters` entries, which RAG's PolicyScopedRecordExecutor
     * binds as a literal SQL WHERE column — an unprojected name here dies
     * live with an "Unknown column" error, not at export time. `created_at`
     * is safe today only because it happens to already be part of
     * PolicyManifestExporter::SORT_FIELDS (already in the projected-columns
     * union via a different path); this guard protects the next typed filter
     * a future task adds, which may not have that coincidental protection.
     *
     * @param  list<string>  $projectedColumns  The resource's ai_v1_* view's exact SELECT-list
     *                                          column names (Projections\ProjectionDefinitions' `columns` —
     *                                          the SAME list assertScopeColumnsProjected() checks against).
     */
    public static function assertTypedFiltersAreProjected(
        BusinessRecordDefinition $definition,
        array $projectedColumns,
    ): void {
        foreach (array_keys($definition->typedFilters) as $field) {
            if (! in_array($field, $projectedColumns, true)) {
                throw new InvalidArgumentException(
                    "Resource [{$definition->resourceType}]'s typed filter [{$field}] is not a column ".
                    "projected by its ai_v1_{$definition->resourceType} view."
                );
            }
        }
    }

    /**
     * @param  list<string>  $projectedColumns  The resource's ai_v1_* view's exact SELECT-list
     *                                          column names (Projections\ProjectionDefinitions' `columns`).
     */
    public static function assertScopeColumnsProjected(
        BusinessRecordDefinition $definition,
        array $projectedColumns,
    ): void {
        foreach ([
            'entity' => $definition->scopeEntityColumn,
            'department' => $definition->scopeDepartmentColumn,
        ] as $role => $column) {
            if ($column === null) {
                continue;
            }

            if (! in_array($column, $projectedColumns, true)) {
                throw new InvalidArgumentException(
                    "Resource [{$definition->resourceType}]'s declared {$role} scope column ".
                    "[{$column}] is not projected by its ai_v1_{$definition->resourceType} view."
                );
            }
        }
    }

    /**
     * `entity: null` is only permitted when the registry has explicitly
     * justified it — no resource type does today (every one of the ten
     * ai_v1_* views carries a bindable entity_id column).
     */
    public static function assertEntityScopeColumnJustified(BusinessRecordDefinition $definition): void
    {
        if ($definition->scopeEntityColumn === null && ! $definition->scopeEntityColumnNullJustified) {
            throw new InvalidArgumentException(
                "Resource [{$definition->resourceType}] declares a null entity scope column without ".
                'the required scopeEntityColumnNullJustified flag.'
            );
        }
    }

    /**
     * @param  list<string>  $readableFields  PolicyManifestExporter::readableFields() for this resource type.
     */
    public static function assertFieldSetFieldsAreReadable(
        BusinessRecordDefinition $definition,
        array $readableFields,
    ): void {
        foreach ($definition->fieldSetFields as $setName => $fields) {
            foreach ($fields as $field) {
                if (! in_array($field, $readableFields, true)) {
                    throw new InvalidArgumentException(
                        "Resource [{$definition->resourceType}]'s field_set_fields[{$setName}] member ".
                        "[{$field}] is not in readable_fields."
                    );
                }
            }
        }
    }

    /**
     * Task B8 / R3 — the gate a live e2e run proved missing (2026-07-24): a
     * field_set_fields member that is not an actual column of the resource's
     * ai_v1_* projection view. RAG's PolicyScopedRecordExecutor consumes
     * field_set_fields verbatim as SQL column names (`table.c[field]`) —
     * a manifest that declares a relation-path label here instead of the
     * flattened column name produces a live "Unknown column" SQL error the
     * FIRST time that field_set is queried, not an export-time failure.
     *
     * @param  array<string,list<string>>  $flattenedFieldSetFields  $definition->fieldSetFields with every
     *                                                               member already passed through
     *                                                               ProjectionDefinitions::outputColumnFor()
     *                                                               — the SAME flattening the manifest's
     *                                                               `field_set_fields` JSON key emits (see
     *                                                               PolicyManifestExporter::export()).
     * @param  list<string>  $projectedColumns  The resource's ai_v1_* view's exact SELECT-list column
     *                                          names (Projections\ProjectionDefinitions' `columns` — the
     *                                          SAME list assertScopeColumnsProjected() checks against).
     */
    public static function assertFieldSetFieldsAreProjected(
        BusinessRecordDefinition $definition,
        array $flattenedFieldSetFields,
        array $projectedColumns,
    ): void {
        foreach ($flattenedFieldSetFields as $setName => $fields) {
            foreach ($fields as $field) {
                if (! in_array($field, $projectedColumns, true)) {
                    throw new InvalidArgumentException(
                        "Resource [{$definition->resourceType}]'s field_set_fields[{$setName}] member ".
                        "[{$field}] is not a column projected by its ai_v1_{$definition->resourceType} view."
                    );
                }
            }
        }
    }
}
