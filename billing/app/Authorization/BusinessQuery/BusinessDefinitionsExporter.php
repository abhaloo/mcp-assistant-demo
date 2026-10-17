<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

use App\Authorization\BusinessQuery\Catalog\DefaultUiDestinations;
use App\Authorization\BusinessQuery\Catalog\UiDestination;
use App\Authorization\PolicyManifestCompatibilityWindow;
use App\Authorization\PolicyManifestExporter;
use Illuminate\Support\Facades\URL;
use RuntimeException;

/**
 * Canonical, content-addressed business-definitions bundle exporter.
 *
 * Hash is sha256 of the canonically ordered payload with `content_hash`
 * excluded (same discipline as PolicyManifestExporter). JSON keys are
 * snake_case for the RAG DefinitionBundle consumer.
 */
final class BusinessDefinitionsExporter
{
    private const string BUSINESS_TIMEZONE = 'Africa/Dar_es_Salaam';

    private const string SCHEMA_VERSION = 'business-definitions/v1';

    private const int COMPATIBILITY_EPOCH = 3;

    /**
     * Immutable revision registered by BackfillAiDetailAttributes for the B1
     * print.sides enum. The definition hash is part of the signed cross-repo
     * contract; changing it requires a new revision and a new bundle.
     */
    private const string PRINT_SIDES_REVISION_HASH = 'sha256:426049d6307980a4937a4a9cf8a75eeb890206c73fb8181cf5a3a24a27409575';

    private const string ORDERED_QTY_REVISION_HASH = 'sha256:ae58e5c089e679b7dd9b17a6cf4d28ae64b49f96b8d5a277e8c3b0502f23865e';

    /** @var list<string> */
    private const array SQL_KEYWORDS = [
        'SUM', 'AVG', 'MIN', 'MAX', 'COUNT', 'COALESCE', 'CASE', 'WHEN', 'THEN',
        'ELSE', 'END', 'IN', 'NOT', 'AND', 'OR', 'IS', 'NULL', 'TRUE', 'FALSE',
        'AS', 'ON', 'BETWEEN', 'LIKE',
    ];

    public function __construct(
        private readonly BusinessDefinitionRegistry $registry = new BusinessDefinitionRegistry,
        private readonly ?PolicyManifestExporter $policyManifest = null,
        private readonly ?PolicyManifestCompatibilityWindow $policyWindow = null,
    ) {}

    /**
     * Registry rules plus semantic-view column checks. This is the export-safe gate.
     * `BusinessDefinitionRegistry::validate()` alone is not sufficient.
     *
     * @return list<string>
     */
    public function validateAll(): array
    {
        return array_merge(
            $this->registry->validate(),
            $this->validateAgainstSemanticViews(),
        );
    }

    /**
     * Payload without content_hash — the hash input.
     *
     * @return array<string, mixed>
     */
    public function export(): array
    {
        $violations = $this->validateAll();
        if ($violations !== []) {
            throw new RuntimeException(
                'Business definition bundle validation failed: '.implode('; ', $violations),
            );
        }

        $definitions = $this->registry->definitions();
        $policyManifest = $this->policyManifest ?? new PolicyManifestExporter;
        $manifestHash = $policyManifest->hash();
        $manifestHashes = ($this->policyWindow ?? new PolicyManifestCompatibilityWindow)->hashes();
        if ($manifestHashes[0] !== $manifestHash) {
            throw new RuntimeException(
                'Policy manifest compatibility index current hash does not match the current exporter hash.',
            );
        }

        return [
            'schema_version' => self::SCHEMA_VERSION,
            'compatibility_epoch' => self::COMPATIBILITY_EPOCH,
            'business_timezone' => self::BUSINESS_TIMEZONE,
            'compatible_policy_manifest_hashes' => $manifestHashes,
            'detail_definitions' => $this->detailDefinitions(),
            'detail_sources' => $this->detailSources(),
            'detail_families' => [[
                'name' => 'ordered_qty',
                'owning_resource' => 'job',
                'description' => 'Authorized raw work-order quantity from work_orders.ordered_qty; numeric_comparison=unsupported_without_canonical_unit.',
                'value_kind' => 'string',
            ]],
            'ui_destinations' => $this->serializeUiDestinations(),
            'resources' => array_map($this->serializeResource(...), $definitions['resources']),
            'measures' => array_map($this->serializeMeasure(...), $definitions['measures']),
            'dimensions' => array_map($this->serializeDimension(...), $definitions['dimensions']),
            'joins' => array_map($this->serializeJoin(...), $definitions['joins']),
            'bucket_sets' => array_map($this->serializeBucketSet(...), $definitions['bucket_sets']),
            'segments' => array_map($this->serializeSegment(...), $definitions['segments']),
            'capabilities' => array_map($this->serializeCapability(...), $definitions['capabilities']),
        ];
    }

    /**
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

    public function compatibilityEpoch(): int
    {
        return self::COMPATIBILITY_EPOCH;
    }

    /** @return list<array<string, mixed>> */
    private function detailDefinitions(): array
    {
        $families = (array) config('ai_detail.families', []);
        $printSides = (array) ($families['print.sides'] ?? []);
        $orderedQuantity = (array) ($families['ordered_qty'] ?? []);

        return [[
            'family_key' => 'print.sides',
            'revision_hash' => self::PRINT_SIDES_REVISION_HASH,
            'logical_source' => 'job.print',
            'physical_source' => 'ai_v2_bq_detail_attribute_fact',
            'owner_resource' => 'job',
            'owner_column' => 'resource_id',
            'value_column' => 'typed_value',
            'value_kind' => 'enum',
            'cardinality' => 'one_to_one',
            'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
            'family_aliases' => $printSides['family_aliases'] ?? [],
            'aliases' => $printSides['aliases'] ?? [],
            'value_mapping' => [
                '1 side ptg' => 'one_side',
                '1 side' => 'one_side',
                'one side' => 'one_side',
                '1-side' => 'one_side',
                'single side' => 'one_side',
                '1side' => 'one_side',
                '2 sides ptg' => 'two_side',
                '2 sides' => 'two_side',
                'two sides' => 'two_side',
                '2-side' => 'two_side',
                'both sides' => 'two_side',
                '2side' => 'two_side',
                '2 sides ptg.' => 'two_side',
            ],
            'uses_union' => false,
        ], [
            'family_key' => 'ordered_qty',
            'revision_hash' => self::ORDERED_QTY_REVISION_HASH,
            'logical_source' => 'job.ordered_qty',
            'physical_source' => 'ai_v2_bq_detail_attribute_fact',
            'owner_resource' => 'job',
            'owner_column' => 'resource_id',
            'value_column' => 'typed_value',
            'value_kind' => 'string',
            'cardinality' => 'one_to_one',
            'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
            'family_aliases' => $orderedQuantity['family_aliases'] ?? [],
            'aliases' => $orderedQuantity['aliases'] ?? [],
            // Empty mappings must serialize as an object (`{}`), not a JSON
            // list (`[]`); RAG validates this field as a dictionary.
            'value_mapping' => new \stdClass,
            'uses_union' => false,
        ]];
    }

    /** @return list<array<string, mixed>> */
    private function detailSources(): array
    {
        $columns = SemanticViews::COLUMNS['ai_v2_bq_detail_attribute_fact'];

        return [[
            'family_key' => 'print.sides',
            'projection_view' => 'ai_v2_bq_detail_attribute_fact',
            'owner_resource' => 'job',
            'owner_column' => 'resource_id',
            'grain' => 'job',
            'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
            'typed_value_column' => 'typed_value',
            'display_value_column' => 'display_value',
            'columns' => $columns,
            'uses_union' => false,
        ], [
            'family_key' => 'ordered_qty',
            'projection_view' => 'ai_v2_bq_detail_attribute_fact',
            'owner_resource' => 'job',
            'owner_column' => 'resource_id',
            'grain' => 'job',
            'scope_columns' => ['entity' => 'entity_id', 'department' => 'department_id'],
            'typed_value_column' => 'typed_value',
            'display_value_column' => 'display_value',
            'columns' => $columns,
            'uses_union' => false,
        ]];
    }

    /**
     * Full artifact bytes: canonical payload + content_hash field.
     */
    public function canonicalJsonWithHash(): string
    {
        $payload = $this->export();
        $payload['content_hash'] = $this->hash();

        return json_encode(
            $this->canonicalize($payload),
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR,
        );
    }

    /**
     * Cross-check every measure/dimension/bucket expression against SemanticViews columns.
     *
     * @return list<string>
     */
    public function validateAgainstSemanticViews(): array
    {
        $violations = [];
        $definitions = $this->registry->definitions();
        $viewsByResource = [];
        foreach ($definitions['resources'] as $binding) {
            $viewsByResource[$binding->name] = $binding->projectionView;
            if (! array_key_exists($binding->projectionView, SemanticViews::COLUMNS)) {
                $violations[] = "resource [{$binding->name}] projectionView [{$binding->projectionView}] is not a SemanticViews column set";
            }
        }

        foreach ($definitions['measures'] as $measure) {
            $view = $viewsByResource[$measure->owningResource] ?? null;
            if ($view === null || ! isset(SemanticViews::COLUMNS[$view])) {
                $violations[] = "measure [{$measure->name}] owningResource [{$measure->owningResource}] has no semantic view";

                continue;
            }
            $allowed = SemanticViews::COLUMNS[$view];
            foreach (self::referencedColumns($measure->sqlExpression) as $column) {
                if (! in_array($column, $allowed, true)) {
                    $violations[] = "measure [{$measure->name}] sqlExpression references [{$column}] not on [{$view}]";
                }
            }
            if ($measure->filterSql !== null) {
                foreach (self::referencedColumns($measure->filterSql) as $column) {
                    if (! in_array($column, $allowed, true)) {
                        $violations[] = "measure [{$measure->name}] filterSql references [{$column}] not on [{$view}]";
                    }
                }
            }
        }

        foreach ($definitions['dimensions'] as $dimension) {
            $view = $viewsByResource[$dimension->owningResource] ?? null;
            if ($view === null || ! isset(SemanticViews::COLUMNS[$view])) {
                $violations[] = "dimension [{$dimension->name}] owningResource [{$dimension->owningResource}] has no semantic view";

                continue;
            }
            $allowed = SemanticViews::COLUMNS[$view];
            foreach (self::referencedColumns($dimension->sqlExpression) as $column) {
                if (! in_array($column, $allowed, true)) {
                    $violations[] = "dimension [{$dimension->name}] sqlExpression references [{$column}] not on [{$view}]";
                }
            }
        }

        foreach ($definitions['bucket_sets'] as $set) {
            $view = $viewsByResource[$set->owningResource] ?? null;
            if ($view === null || ! isset(SemanticViews::COLUMNS[$view])) {
                $violations[] = "bucket_set [{$set->name}] owningResource [{$set->owningResource}] has no semantic view";

                continue;
            }
            $allowed = SemanticViews::COLUMNS[$view];
            if ($set->dimension !== '' && ! in_array($set->dimension, $allowed, true)) {
                $violations[] = "bucket_set [{$set->name}] dimension [{$set->dimension}] not on [{$view}]";
            }
            foreach (self::referencedColumns($set->appliesFilterSql) as $column) {
                if (! in_array($column, $allowed, true)) {
                    $violations[] = "bucket_set [{$set->name}] appliesFilterSql references [{$column}] not on [{$view}]";
                }
            }
            foreach ($set->bucketPredicates as [$label, $predicate]) {
                foreach (self::referencedColumns($predicate) as $column) {
                    if (! in_array($column, $allowed, true)) {
                        $violations[] = "bucket_set [{$set->name}] bucket [{$label}] references [{$column}] not on [{$view}]";
                    }
                }
            }
        }

        foreach ($definitions['segments'] as $segment) {
            $view = $viewsByResource[$segment->owningResource] ?? null;
            if ($view === null || ! isset(SemanticViews::COLUMNS[$view])) {
                $violations[] = "segment [{$segment->name}] owningResource [{$segment->owningResource}] has no semantic view";

                continue;
            }
            $allowed = SemanticViews::COLUMNS[$view];
            foreach (self::referencedColumns($segment->predicateSql) as $column) {
                if (! in_array($column, $allowed, true)) {
                    $violations[] = "segment [{$segment->name}] predicateSql references [{$column}] not on [{$view}]";
                }
            }
        }

        return $violations;
    }

    /**
     * Extract column-like identifiers from a dialect-free SQL fragment.
     *
     * @return list<string>
     */
    public static function referencedColumns(string $sql): array
    {
        $withoutStrings = preg_replace("/'[^']*'/u", ' ', $sql);
        if ($withoutStrings === null) {
            throw new RuntimeException(
                'Failed to strip SQL string literals (PCRE): '.preg_last_error_msg(),
            );
        }
        preg_match_all('/[A-Za-z_][A-Za-z0-9_]*/', $withoutStrings, $matches);

        $columns = [];
        foreach ($matches[0] as $token) {
            if (in_array(strtoupper($token), self::SQL_KEYWORDS, true)) {
                continue;
            }
            $columns[$token] = true;
        }

        return array_keys($columns);
    }

    /** @return list<array<string, mixed>> */
    private function serializeUiDestinations(): array
    {
        $destinations = DefaultUiDestinations::all();
        usort($destinations, fn (UiDestination $left, UiDestination $right): int => strcmp($left->key, $right->key));

        return array_map($this->serializeUiDestination(...), $destinations);
    }

    /** @return array<string, mixed> */
    private function serializeUiDestination(UiDestination $destination): array
    {
        $href = URL::route($destination->routeName, $destination->routeParams, false);
        if ($destination->query !== []) {
            $href .= (str_contains($href, '?') ? '&' : '?').http_build_query($destination->query);
        }

        return [
            'key' => $destination->key,
            'kind' => $destination->kind,
            'label' => $destination->label,
            'aliases' => $destination->aliases,
            'href' => $href,
            'link_permissions' => $destination->linkPermissions,
            'link_permission_mode' => $destination->linkPermissionMode,
        ];
    }

    /** @return array<string, mixed> */
    private function serializeResource(ResourceBinding $binding): array
    {
        $payload = [
            'name' => $binding->name,
            'projection_view' => $binding->projectionView,
            'scope_columns' => $binding->scopeColumns,
            'department_scope_mode' => $binding->departmentScopeMode,
            'primary_key' => $binding->primaryKey,
            'record_predicates' => $binding->recordPredicates === []
                ? new \stdClass
                : $binding->recordPredicates,
            'description' => $binding->description,
        ];
        if ($binding->referenceDimension !== null) {
            $payload['reference_dimension'] = $binding->referenceDimension;
        }
        if ($binding->recordRoute !== null) {
            $parameters = $binding->recordRouteParam === null
                ? []
                : [$binding->recordRouteParam => '__ID__'];
            $payload['record_route'] = str_replace(
                '__ID__',
                '{id}',
                URL::route($binding->recordRoute, $parameters, false),
            );
            $payload['link_permissions'] = $binding->linkPermissions;
            $payload['link_permission_mode'] = $binding->linkPermissionMode;
        }
        if ($binding->label !== null) {
            $payload['label'] = $binding->label;
        }

        return $payload;
    }

    /** @return array<string, mixed> */
    private function serializeMeasure(MeasureDefinition $measure): array
    {
        return $measure->toExportArray();
    }

    /** @return array<string, mixed> */
    private function serializeDimension(DimensionDefinition $dimension): array
    {
        return $dimension->toExportArray();
    }

    /** @return array<string, mixed> */
    private function serializeJoin(JoinDefinition $join): array
    {
        return [
            'from_resource' => $join->fromResource,
            'to_resource' => $join->toResource,
            'relationship' => $join->relationship,
            'on_sql' => $join->onSql,
            'optional' => $join->optional,
        ];
    }

    /** @return array<string, mixed> */
    private function serializeBucketSet(BucketSetDefinition $set): array
    {
        return [
            'name' => $set->name,
            'owning_resource' => $set->owningResource,
            'dimension' => $set->dimension,
            'buckets' => $set->buckets,
            'applies_filter_sql' => $set->appliesFilterSql,
            'description' => $set->description,
            'relative_to_business_date' => $set->relativeToBusinessDate,
            'bucket_predicates' => $set->bucketPredicates,
        ];
    }

    /** @return array<string, mixed> */
    private function serializeSegment(SegmentDefinition $segment): array
    {
        return $segment->toExportArray();
    }

    /** @return array<string, mixed> */
    private function serializeCapability(CapabilityEntry $entry): array
    {
        $grantable = $entry->capabilityState === 'enabled' && $entry->requiredPermissions !== [];

        return [
            'name' => $entry->name,
            'kind' => $entry->kind,
            'resolves_to' => $entry->resolvesTo,
            'title' => $entry->title,
            'description' => $entry->description,
            'example_questions' => $entry->exampleQuestions,
            'required_filters' => $entry->requiredFilters,
            'required_permissions' => $entry->requiredPermissions,
            'capability_state' => $entry->capabilityState,
            'grantable' => $grantable,
            'meta' => $entry->meta === [] ? new \stdClass : $entry->meta,
        ];
    }

    /**
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
