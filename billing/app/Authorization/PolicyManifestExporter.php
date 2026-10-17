<?php

declare(strict_types=1);

namespace App\Authorization;

use App\Authorization\Projections\ProjectionDefinitions;
use App\Authorization\Projections\ProjectionViewName;

/**
 * Canonical, deterministic policy-manifest exporter (Task B3 / R2).
 *
 * Describes — per BusinessRecordRegistry resource type — the safe
 * projection a FUTURE `ai_v1_*` database view would expose (a projection
 * NAME + readable columns/relation fields as data; creating the views
 * themselves is a later task, per task-B3-brief.md R2), the entity/
 * department/subtype predicates BusinessRecordDefinition already captures,
 * a conservative set of typed filters/sorts grounded in verified schema
 * (App\Traits\EntityTrait's entity_id column, WorkOrderVisibilityScope's
 * department_id, every backing table's timestamps() — filter field NAMES
 * come from BusinessRecordDefinition::filterFieldNames() as of Task P3; see
 * SORT_FIELDS below for the sort-field inventory), the named
 * field_sets (read from BusinessRecordDefinition::$fieldSets — the SAME
 * source AiAccessPolicy::snapshot() reads, so the two can never drift),
 * the search/read/link permission requirements, and the canonical link
 * key ('id' for all ten resource types today — every
 * GlobalSearchService::registerDefaults() urlBuilder keys off `->id`).
 *
 * Task B6 / R1 additionally emits, per resource: `scope_columns` (the
 * projection column names — `entity`/`department` — RAG's executor binds
 * request-scoped predicates against, read from
 * BusinessRecordDefinition::$scopeEntityColumn/$scopeDepartmentColumn) and
 * `field_set_fields` (the exact field list per named field_set, read from
 * BusinessRecordDefinition::$fieldSetFields — `field_sets` is now DERIVED
 * from these keys rather than a separately-declared list, so the two can
 * never drift apart). export() validates both against reality before
 * returning anything (PolicyManifestValidator — fails the export on a scope
 * column the resource's ai_v1_* view does not actually project, a
 * field_set_fields member outside readable_fields, a field_set_fields member
 * that is not an actual projected column (Task B8 / R3 — see below), or an
 * unjustified null entity scope column) rather than silently emitting a
 * manifest RAG's executor cannot trust.
 *
 * Task B8 / R2 (fix for a live e2e failure, 2026-07-24 — "Unknown column
 * 'ai_v1_invoice.customer.name'"): both `readable_fields` and
 * `field_set_fields` are emitted FLATTENED — every dotted relation-path label
 * (`customer.name`) becomes the real projected column name (`customer_name`),
 * via the SAME `ProjectionDefinitions::outputColumnFor()` helper the view
 * builder itself uses. This is deliberate: RAG's PolicyScopedRecordExecutor
 * binds BOTH manifest keys directly as SQL column names
 * (`table.c[field]` in `_select()`/`_searchable_fields()`), so anything
 * exported under either key must already be a real identifier. The
 * UNFLATTENED, dotted form (`BusinessRecordDefinition::readableFields()`,
 * Task P3) is still what `RecordContextResolver` reads directly, in-process,
 * to resolve Eloquent relations (`$model->{$relation}->{$column}`) — that
 * in-process consumer is untouched by this flattening, which only applies at
 * the JSON manifest boundary.
 *
 * Deterministic by construction: every input is a hardcoded, code-only
 * value (no DB, no clock, no randomness) — two exports of the same code
 * always produce byte-identical canonical output (JSON with keys sorted
 * recursively, no timestamps). `hash()` is `sha256:` + hex digest of that
 * canonical serialization; `database/ai-manifest/policy-manifest.json` +
 * `.hash` are the committed artifact PolicyManifestExporterTest's drift
 * check regenerates and compares against on every run.
 */
final class PolicyManifestExporter
{
    /**
     * @param  ?ProjectionDefinitions  $projections  Task B6 / R1 — the source of truth export()
     *                                               cross-checks every declared scope_columns value against
     *                                               (PolicyManifestValidator::assertScopeColumnsProjected()).
     *                                               Defaults to a fresh instance built from the SAME $registry
     *                                               (never a second, independently-constructed
     *                                               BusinessRecordRegistry) — this is a pure, DB-free,
     *                                               in-memory column-name computation (see that class's own
     *                                               docblock), so constructing it here does not cost this
     *                                               class its zero-DB/framework-dependency discipline.
     */
    public function __construct(
        private readonly BusinessRecordRegistry $registry = new BusinessRecordRegistry,
        private readonly ?ProjectionDefinitions $projections = null,
    ) {}

    /**
     * Task P3 — deleted: the independent READABLE_FIELDS allowlist this
     * class used to maintain. Readable fields now come from
     * BusinessRecordRegistry::get($resourceType)->readableFields() (a
     * definition's own stable, de-duplicated field_set_fields union) — the
     * SAME source AiAccessPolicy::snapshot() and RecordContextResolver both
     * read, so there is exactly one place a resource type's readable-field
     * list is declared, not two that must be kept byte-identical by hand
     * (see BusinessRecordRegistry::billFieldSetFields()'s own prior
     * docblock, which pinned this equality manually before this task).
     */

    /**
     * Task 1C — the sort field names export() declares for EVERY resource's
     * `sorts` entry: the two Eloquent `timestamps()` columns every registry
     * model carries. Single source for both export()'s `sorts` value and
     * ProjectionDefinitions::columnsFor()'s enforcement-column union (via the
     * static sortFields() below) — a hand-copied second list in the
     * projection builder would be exactly the kind of drift this hotfix
     * exists to close.
     *
     * Task P3: sortFields() is now STATIC — ProjectionDefinitions reads this
     * constant via a stateless call (`PolicyManifestExporter::sortFields()`),
     * never by constructing an exporter instance. That is what breaks the
     * exporter/projection construction cycle Task P3 removes: before, each
     * class's constructor built a live instance of the other by default
     * (ProjectionDefinitions' ctor built `new PolicyManifestExporter($registry)`;
     * that exporter's own export() built `new ProjectionDefinitions($registry)`
     * when none was injected). This constant's OWNERSHIP does not move
     * (still exactly one declaration, here) — only how a stateless reader
     * reaches it does.
     *
     * @var list<string>
     */
    private const array SORT_FIELDS = ['created_at', 'updated_at'];

    /**
     * Task P3 — replaces the former instance `readableFields(string $resourceType)`
     * accessor. RecordContextResolver now reads
     * BusinessRecordDefinition::readableFields() directly (no exporter
     * dependency at all — see that class); this method still exists for
     * export()'s own internal use rendered explicit below (kept as a public
     * accessor since PolicyManifestExporterTest::readable_fields_is_pinned_to_the_flattened_column_names
     * and friends assert against the flattened export() output, but a
     * plain-English "what does resource X's manifest consider readable"
     * accessor stays useful for anything that wants the UNFLATTENED,
     * dotted-relation-path form without also wanting a full export()).
     *
     * @return list<string>
     */
    public function readableFields(string $resourceType): array
    {
        return $this->registry->get($resourceType)->readableFields();
    }

    /**
     * Task P3 — the sort field names export() declares for every resource
     * (Task 1C's SORT_FIELDS, see its own docblock). STATIC: this is what
     * lets ProjectionDefinitions read it without constructing a
     * PolicyManifestExporter instance (removing the exporter/projection
     * construction cycle — see SORT_FIELDS' docblock).
     *
     * @return list<string>
     */
    public static function sortFields(): array
    {
        return self::SORT_FIELDS;
    }

    /**
     * Task B1b / ADR 0031 twin — every request-scoped time comparison RAG's
     * executor performs against a Billing business period (created_at range
     * filters, Africa/Dar_es_Salaam calendar boundaries — see ADR 0031) must
     * agree with Billing's own canonical timezone, not assume UTC. Emitted
     * once at the manifest root (not per-resource — it is one Billing-wide
     * fact, not a per-record property).
     */
    private const string BUSINESS_TIMEZONE = 'Africa/Dar_es_Salaam';

    /**
     * @return array{business_timezone: string, resources: array<string, array<string, mixed>>}
     */
    public function export(): array
    {
        $resources = [];
        // Built ONCE per export() call (not per resource type) — ProjectionDefinitions::all()
        // builds all ten view definitions together; calling it inside the loop below would be
        // an O(n^2) rebuild for no benefit, since every branch is pure/cheap but still wasted work.
        $projectionsByResource = ($this->projections ?? new ProjectionDefinitions($this->registry))->all();

        foreach ($this->registry->resourceTypes() as $resourceType) {
            $definition = $this->registry->get($resourceType);
            $readableFields = $definition->readableFields();

            // Task B8 / R2 (fix for the field-name parity bug a live e2e run
            // caught 2026-07-24): everything this manifest emits under
            // `readable_fields`/`field_set_fields` crosses into RAG's world as
            // a LITERAL SQL column name (PolicyScopedRecordExecutor._select /
            // ._searchable_fields bind `table.c[field]` directly) — so both
            // are flattened here via the SAME ProjectionDefinitions::
            // outputColumnFor() the view builder already uses for its SELECT
            // list ('customer.name' -> 'customer_name'). This is deliberately
            // NOT a rename of BusinessRecordDefinition::fieldSetFields/
            // readableFields() themselves (Task P3) — RecordContextResolver
            // reads BusinessRecordDefinition::readableFields() directly,
            // UNFLATTENED, in-process, to eager-load Eloquent relations and
            // resolve `$model->{$relation}->{$column}` values, which
            // genuinely needs the dotted relation-path form. Two different
            // vocabularies for two different consumers of the SAME declared
            // field list — flattened only at the ONE boundary (the exported
            // JSON manifest) where it becomes a SQL identifier.
            $flattenedReadableFields = array_map(ProjectionDefinitions::outputColumnFor(...), $readableFields);
            $flattenedFieldSetFields = array_map(
                fn (array $fields): array => array_map(ProjectionDefinitions::outputColumnFor(...), $fields),
                $definition->fieldSetFields,
            );

            // Task B6 / R1 + B1b / F4/R1: fail the export (never silently emit) on a
            // bad scope-column, field_set_fields, department-scope-mode,
            // typed-filter-operator, sort-direction, or groupable-field declaration.
            // See PolicyManifestValidator's own docblock for the full rule list.
            PolicyManifestValidator::assertDepartmentScopeModeIsDeclaredWhenColumnPresent($definition);
            PolicyManifestValidator::assertDepartmentScopeModeIsSupported($definition);
            PolicyManifestValidator::assertOperationsAreSupported($definition);
            PolicyManifestValidator::assertTypedFilterOperatorsAreSupported($definition);
            PolicyManifestValidator::assertSortDirectionsAreSupported($definition);
            PolicyManifestValidator::assertGroupOperationsHaveGroupableFields($definition);
            // Task B1b fix-round-1 / I2 — typedFilters is not part of filterFieldNames(), so
            // ProjectionDefinitions::columnsFor() never cross-checks it; this is the ONE guard
            // that does. Runs against the SAME real projected-columns list assertScopeColumnsProjected()
            // and assertFieldSetFieldsAreProjected() already trust below.
            PolicyManifestValidator::assertTypedFiltersAreProjected(
                $definition,
                $projectionsByResource[$resourceType]->columns,
            );
            PolicyManifestValidator::assertEntityScopeColumnJustified($definition);
            PolicyManifestValidator::assertScopeColumnsProjected(
                $definition,
                $projectionsByResource[$resourceType]->columns,
            );
            PolicyManifestValidator::assertFieldSetFieldsAreReadable($definition, $readableFields);
            // Task B8 / R3: the gate the 2026-07-24 live e2e failure proved
            // missing — the FLATTENED field_set_fields (what the manifest
            // actually emits) checked against the resource's REAL projected
            // view columns, closing the exact gap that let a dotted
            // relation-path label reach RAG's executor as a SQL column name.
            PolicyManifestValidator::assertFieldSetFieldsAreProjected(
                $definition,
                $flattenedFieldSetFields,
                $projectionsByResource[$resourceType]->columns,
            );
            PolicyManifestValidator::assertGroupableFieldsAreReadable($definition, $flattenedReadableFields);

            // Task B1b / F4 — the ONLY place a null (undeclared)
            // departmentScopeMode ever resolves to a concrete value: a
            // resource with no department column has nothing to scope, so
            // "undeclared" and "none" are equivalent for it. A resource
            // WITH a department column can never reach this null branch —
            // assertDepartmentScopeModeIsDeclaredWhenColumnPresent() above
            // already threw. The manifest itself never emits a missing/null
            // department_scope_mode — RAG's consumer denies a resource
            // outright if it ever saw one (task-B1b-brief.md requirement 4).
            $effectiveDepartmentScopeMode = $definition->departmentScopeMode
                ?? BusinessRecordDefinition::DEPARTMENT_SCOPE_NONE;

            $resources[$resourceType] = [
                'canonical_link_key' => 'id',
                'department_scope_mode' => $effectiveDepartmentScopeMode,
                'entity_scope' => $definition->entityScope,
                // Derived from field_set_fields' keys (Task B6 / R1) — same values as
                // before B6 (PolicyManifestExporterTest pins this), now guaranteed to
                // never drift from field_set_fields' own declared sets.
                'field_sets' => array_keys($definition->fieldSetFields),
                'field_set_fields' => $flattenedFieldSetFields,
                'filters' => $this->filtersFor($definition),
                'groupable_fields' => $definition->groupableFields,
                'link_permission_mode' => $definition->effectiveLinkPermissionMode(),
                'link_permissions' => $definition->effectiveLinkPermissions(),
                'model_class' => $definition->modelClass,
                'operations' => $definition->operations,
                'parent_relation' => $definition->parentRelation,
                'projection_name' => ProjectionViewName::forResource($resourceType),
                'read_permission_mode' => $definition->readPermissionMode,
                'read_permissions' => $definition->readPermissions,
                'readable_fields' => $flattenedReadableFields,
                // json_encode([]) === '[]', a JSON array — record_predicates is a
                // column => value MAP, so an empty one must encode as '{}', matching
                // the same wire-shape fix applied to AiAccessPolicy::snapshot()'s
                // `resources` field (Critical review fix, Task B3).
                'record_predicates' => $definition->recordPredicates === []
                    ? new \stdClass
                    : $definition->recordPredicates,
                'scope_columns' => [
                    'entity' => $definition->scopeEntityColumn,
                    'department' => $definition->scopeDepartmentColumn,
                ],
                'search_permission' => $definition->searchPermission,
                'sorts' => $this->sortsFor($definition),
            ];
        }

        return [
            'business_timezone' => self::BUSINESS_TIMEZONE,
            'resources' => $resources,
        ];
    }

    /**
     * Task P3 — field NAMES now come from
     * BusinessRecordDefinition::filterFieldNames() (relocated from the
     * former private HAS_OWN_ENTITY_ID const + this method's own
     * resourceType-keyed branch); this method's only remaining job is
     * attaching the manifest's `type` metadata per field.
     *
     * Task B1b / R1 — appends $definition->typedFilters as ADDITIONAL filter
     * entries beyond that base set, each carrying an explicit `operators`
     * list (legacy filters above stay `{field, type}` only — an absent
     * `operators` key means `['eq']` to the consumer, unchanged wire shape).
     * Every typedFilters field this task declares (job's `created_at`) is
     * already a projected ai_v1_* column via SORT_FIELDS, so no new
     * projection-view work is needed to keep this filter queryable.
     *
     * @return list<array{field: string, type: string, operators?: list<string>}>
     */
    private function filtersFor(BusinessRecordDefinition $definition): array
    {
        $baseFilters = array_map(
            static fn (string $field): array => [
                'field' => $field,
                'type' => in_array($field, ['id', 'entity_id', 'department_id'], true) ? 'int' : 'string',
            ],
            $definition->filterFieldNames(),
        );

        $typedFilters = [];
        foreach ($definition->typedFilters as $field => $spec) {
            $typedFilters[] = [
                'field' => $field,
                'type' => $spec['type'],
                'operators' => $spec['operators'],
            ];
        }

        return [...$baseFilters, ...$typedFilters];
    }

    /**
     * Task 1C's SORT_FIELDS (created_at/updated_at, every resource), now
     * carrying an optional Task B1b / R1 `directions` override per field —
     * absent means `['asc']` to the consumer (unchanged legacy wire shape);
     * job's `created_at` is the only field any resource overrides today
     * ($definition->sortDirectionOverrides, ADR 0030).
     *
     * @return list<array{field: string, type: string, directions?: list<string>}>
     */
    private function sortsFor(BusinessRecordDefinition $definition): array
    {
        return array_map(
            static function (string $field) use ($definition): array {
                $entry = ['field' => $field, 'type' => 'datetime'];

                if (array_key_exists($field, $definition->sortDirectionOverrides)) {
                    $entry['directions'] = $definition->sortDirectionOverrides[$field];
                }

                return $entry;
            },
            self::SORT_FIELDS,
        );
    }

    /**
     * The canonical, recursively key-sorted export — what both `hash()` and
     * the committed drift-check artifact are built from.
     *
     * @return array<string, mixed>
     */
    public function canonicalArray(): array
    {
        return $this->canonicalize($this->export());
    }

    public function canonicalJson(): string
    {
        return json_encode(
            $this->canonicalArray(),
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
        );
    }

    public function hash(): string
    {
        return 'sha256:'.hash('sha256', $this->canonicalJson());
    }

    /**
     * Recursively sorts associative (string-keyed) array keys; leaves list
     * (sequential int-keyed) arrays' element ORDER untouched — order is
     * semantically meaningful there (e.g. `filters`/`sorts`/`readable_fields`
     * entries), only key NAMES need sorting for a stable serialization.
     *
     * @param  array<array-key, mixed>  $value
     * @return array<array-key, mixed>
     */
    private function canonicalize(array $value): array
    {
        if (array_is_list($value)) {
            return array_map(
                fn (mixed $item): mixed => is_array($item) ? $this->canonicalize($item) : $item,
                $value,
            );
        }

        ksort($value);

        return array_map(
            fn (mixed $item): mixed => is_array($item) ? $this->canonicalize($item) : $item,
            $value,
        );
    }
}
