<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

use App\Authorization\BusinessQuery\Catalog\CatalogConstants;
use App\Authorization\BusinessQuery\Catalog\DefaultBucketSets;
use App\Authorization\BusinessQuery\Catalog\DefaultCapabilities;
use App\Authorization\BusinessQuery\Catalog\DefaultDimensions;
use App\Authorization\BusinessQuery\Catalog\DefaultJoins;
use App\Authorization\BusinessQuery\Catalog\DefaultMeasures;
use App\Authorization\BusinessQuery\Catalog\DefaultResources;
use App\Authorization\BusinessQuery\Catalog\DefaultSegments;
use Database\Seeders\PermissionTableSeeder;
use Illuminate\Support\Facades\Route;
use InvalidArgumentException;

/**
 * Injectable registry of business-query definitions (measures, dimensions, joins,
 * bucket sets, segments, resource bindings, capability entries).
 *
 * Deliberately NOT final — ExportAiBusinessDefinitionsTest (B3) injects a
 * subclass / mutated instance to prove export hashes change with content.
 */
class BusinessDefinitionRegistry
{
    /** @var array{
     *   resources: list<ResourceBinding>,
     *   measures: list<MeasureDefinition>,
     *   dimensions: list<DimensionDefinition>,
     *   joins: list<JoinDefinition>,
     *   bucket_sets: list<BucketSetDefinition>,
     *   segments: list<SegmentDefinition>,
     *   capabilities: list<CapabilityEntry>
     * } */
    private array $definitions;

    /**
     * Every group is required when a set is supplied: an omitted group used to be
     * back-filled from the defaults, which let a caller ship a half-built registry.
     *
     * @param  array{
     *   resources: list<ResourceBinding>,
     *   measures: list<MeasureDefinition>,
     *   dimensions: list<DimensionDefinition>,
     *   joins: list<JoinDefinition>,
     *   bucket_sets: list<BucketSetDefinition>,
     *   segments: list<SegmentDefinition>,
     *   capabilities: list<CapabilityEntry>
     * }|null  $definitions
     */
    public function __construct(?array $definitions = null)
    {
        if ($definitions === null) {
            $this->definitions = self::buildDefaults();

            return;
        }
        foreach (['resources', 'measures', 'dimensions', 'joins', 'bucket_sets', 'segments', 'capabilities'] as $group) {
            if (! array_key_exists($group, $definitions)) {
                throw new InvalidArgumentException("Definition set is missing the [{$group}] group.");
            }
        }
        $this->definitions = $definitions;
    }

    public static function default(): self
    {
        return new self;
    }

    /**
     * Raw catalog snapshot. Not export-safe on its own — publish through
     * BusinessDefinitionsExporter::validateAll() / export(), which throw.
     *
     * @return array{
     *   resources: list<ResourceBinding>,
     *   measures: list<MeasureDefinition>,
     *   dimensions: list<DimensionDefinition>,
     *   joins: list<JoinDefinition>,
     *   bucket_sets: list<BucketSetDefinition>,
     *   segments: list<SegmentDefinition>,
     *   capabilities: list<CapabilityEntry>
     * }
     */
    public function definitions(): array
    {
        return $this->definitions;
    }

    public function segment(string $name): SegmentDefinition
    {
        foreach ($this->definitions['segments'] as $segment) {
            if ($segment->name === $name) {
                return $segment;
            }
        }

        throw new InvalidArgumentException("Unknown segment [{$name}].");
    }

    public function measure(string $name): MeasureDefinition
    {
        foreach ($this->definitions['measures'] as $measure) {
            if ($measure->name === $name) {
                return $measure;
            }
        }

        throw new InvalidArgumentException("Unknown measure [{$name}].");
    }

    public function bucketSet(string $name): BucketSetDefinition
    {
        foreach ($this->definitions['bucket_sets'] as $set) {
            if ($set->name === $name) {
                return $set;
            }
        }

        throw new InvalidArgumentException("Unknown bucket set [{$name}].");
    }

    public function binding(string $resource): ResourceBinding
    {
        foreach ($this->definitions['resources'] as $binding) {
            if ($binding->name === $resource) {
                return $binding;
            }
        }

        throw new InvalidArgumentException("Unknown resource [{$resource}].");
    }

    public function primaryKeyFor(string $resource): ?string
    {
        foreach ($this->definitions['resources'] as $binding) {
            if ($binding->name === $resource) {
                return $binding->primaryKey;
            }
        }

        return null;
    }

    /** @return list<string> */
    public function validate(): array
    {
        $violations = [];
        $resourceNames = [];
        foreach ($this->definitions['resources'] as $binding) {
            $resourceNames[$binding->name] = $binding;
        }

        foreach ($resourceNames as $binding) {
            if ($binding->recordRoute === null) {
                continue;
            }
            if ($binding->linkPermissions === []) {
                $violations[] = "resource [{$binding->name}] declares a recordRoute without linkPermissions";
            }
            foreach ($binding->linkPermissions as $permission) {
                if (! in_array($permission, self::permissionCatalog(), true)) {
                    $violations[] = "resource [{$binding->name}] linkPermission [{$permission}] not in PermissionTableSeeder";
                }
            }
            if (! Route::has($binding->recordRoute)) {
                $violations[] = "resource [{$binding->name}] recordRoute [{$binding->recordRoute}] is not a registered route";
            }
        }

        foreach ($this->definitions['joins'] as $join) {
            if (! isset($resourceNames[$join->fromResource]) || $resourceNames[$join->fromResource]->primaryKey === '') {
                $violations[] = "join fromResource [{$join->fromResource}] has no primary key";
            }
            if (! isset($resourceNames[$join->toResource]) || $resourceNames[$join->toResource]->primaryKey === '') {
                $violations[] = "join toResource [{$join->toResource}] has no primary key";
            }
        }

        $memberNames = [];
        $timeDimensions = [];
        $dimensionNames = [];
        foreach (['measures', 'dimensions', 'bucket_sets', 'segments'] as $group) {
            foreach ($this->definitions[$group] as $member) {
                if (isset($memberNames[$member->name])) {
                    $violations[] = "duplicate member name [{$member->name}] across measures/dimensions/bucket_sets/segments";
                }
                $memberNames[$member->name] = $group;

                if (! isset($resourceNames[$member->owningResource])) {
                    $violations[] = "{$group} [{$member->name}] owningResource [{$member->owningResource}] is not a declared resource";
                }

                if ($group === 'dimensions') {
                    $dimensionNames[$member->name] = true;
                    if ($member->type === 'time') {
                        $timeDimensions[$member->name] = true;
                    }
                }
            }
        }

        $dimensionsByResourceAndLabel = [];
        foreach ($this->definitions['dimensions'] as $dimension) {
            if ($dimension->label !== null) {
                $dimensionsByResourceAndLabel[$dimension->owningResource][$dimension->label][] = $dimension;
            }
        }

        foreach ($dimensionsByResourceAndLabel as $resource => $byLabel) {
            foreach ($byLabel as $label => $dimensions) {
                $count = count($dimensions);
                for ($i = 0; $i < $count; $i++) {
                    for ($j = $i + 1; $j < $count; $j++) {
                        $a = $dimensions[$i];
                        $b = $dimensions[$j];
                        if ($a->displayOf !== $b->name && $b->displayOf !== $a->name) {
                            $violations[] = "dimensions [{$a->name}] and [{$b->name}] on [{$resource}] share label [{$label}] without a displayOf link";
                        }
                    }
                }
            }
        }

        $resolvable = array_keys($memberNames);

        foreach ($this->definitions['capabilities'] as $entry) {
            if (! in_array($entry->resolvesTo, $resolvable, true)) {
                $violations[] = "capability [{$entry->name}] resolvesTo [{$entry->resolvesTo}] is undeclared";
            }
            foreach ($entry->requiredPermissions as $permission) {
                if (! in_array($permission, self::permissionCatalog(), true)) {
                    $violations[] = "capability [{$entry->name}] requiredPermission [{$permission}] not in PermissionTableSeeder";
                }
            }
        }

        $measuresByName = [];
        foreach ($this->definitions['measures'] as $measure) {
            $measuresByName[$measure->name] = $measure;
        }

        $dimensionsByName = [];
        foreach ($this->definitions['dimensions'] as $dimension) {
            $dimensionsByName[$dimension->name] = $dimension;
        }

        foreach ($this->definitions['dimensions'] as $dimension) {
            if ($dimension->linkKey !== null && ! isset($resourceNames[$dimension->linkKey])) {
                $violations[] = "dimension [{$dimension->name}] linkKey [{$dimension->linkKey}] is not a declared resource";
            }
            if ($dimension->linkVia !== null) {
                $target = $dimensionsByName[$dimension->linkVia] ?? null;
                $isKey = $target !== null
                    && $target->owningResource === $dimension->owningResource
                    && ($target->isPrimaryKey || ($target->format === 'id' && $target->linkKey !== null));
                if (! $isKey) {
                    $violations[] = "dimension [{$dimension->name}] linkVia [{$dimension->linkVia}] must be an id dimension of [{$dimension->owningResource}] that links or is its primary key";
                }
            }
        }

        foreach ($this->definitions['measures'] as $measure) {
            if ($measure->aggType === 'derived') {
                if ($measure->expressionKind === null || ! in_array($measure->expressionKind, ['ratio', 'share'], true)) {
                    $violations[] = "derived measure [{$measure->name}] expressionKind must be ratio or share";
                }
                if ($measure->sqlExpression !== '') {
                    $violations[] = "derived measure [{$measure->name}] sqlExpression must be empty";
                }
                if ($measure->filterSql !== null) {
                    $violations[] = "derived measure [{$measure->name}] filterSql must be null";
                }
                if ($measure->format !== null && ! in_array($measure->format, ['percent', 'number'], true)) {
                    $violations[] = "derived measure [{$measure->name}] format must be percent or number";
                }

                if ($measure->expressionKind === 'ratio' && count($measure->derivedFrom) !== 2) {
                    $violations[] = "derived measure [{$measure->name}] with expressionKind [ratio] must have exactly 2 components";
                } elseif ($measure->expressionKind === 'share' && count($measure->derivedFrom) !== 1) {
                    $violations[] = "derived measure [{$measure->name}] with expressionKind [share] must have exactly 1 component";
                }

                foreach ($measure->derivedFrom as $compName) {
                    if (! isset($measuresByName[$compName])) {
                        $violations[] = "derived measure [{$measure->name}] component [{$compName}] is undeclared";
                    } else {
                        $comp = $measuresByName[$compName];
                        if ($comp->aggType === 'derived') {
                            $violations[] = "derived measure [{$measure->name}] component [{$compName}] cannot be derived";
                        }
                        if ($comp->owningResource !== $measure->owningResource) {
                            $violations[] = "derived measure [{$measure->name}] component [{$compName}] is not owned by [{$measure->owningResource}]";
                        }
                    }
                }
            }

            if ($measure->timeDimension !== null && ! isset($timeDimensions[$measure->timeDimension])) {
                $violations[] = "measure [{$measure->name}] timeDimension [{$measure->timeDimension}] is not a declared time dimension";
            }
            foreach ($measure->allowedTimeAxes as $axis) {
                if (! isset($timeDimensions[$axis])) {
                    $violations[] = "measure [{$measure->name}] allowedTimeAxes [{$axis}] is not a declared time dimension";
                }
            }
        }

        foreach ($this->definitions['segments'] as $segment) {
            if ($segment->predicateSql === '') {
                $violations[] = "segment [{$segment->name}] predicateSql must not be empty";
            }
            if (! in_array($segment->capabilityState, ['enabled', 'decision_pending', 'unsupported'], true)) {
                $violations[] = "segment [{$segment->name}] has invalid capabilityState [{$segment->capabilityState}]";
            }
        }

        foreach ($this->definitions['dimensions'] as $dimension) {
            if ($dimension->resolvableAs !== null
                && ! in_array($dimension->resolvableAs, CatalogConstants::RESOLVABLE_TYPES, true)) {
                $violations[] = "dimension [{$dimension->name}] resolvableAs [{$dimension->resolvableAs}] is not a v1 resolver type";
            }
            foreach ($dimension->directRelationships as $related) {
                if (! isset($dimensionNames[$related])) {
                    $violations[] = "dimension [{$dimension->name}] directRelationships [{$related}] is undeclared";
                }
            }
        }

        foreach ($this->definitions['bucket_sets'] as $set) {
            $prev = null;
            $count = count($set->buckets);
            foreach ($set->buckets as $i => [$label, $bound]) {
                if ($i === $count - 1) {
                    if ($bound !== null) {
                        $violations[] = "bucket set [{$set->name}] final bound must be null";
                    }
                } else {
                    if ($bound === null) {
                        $violations[] = "bucket set [{$set->name}] non-final bucket [{$label}] has null bound";
                    } elseif ($prev !== null && $bound <= $prev) {
                        $violations[] = "bucket set [{$set->name}] bounds must be strictly increasing";
                    }
                    $prev = $bound;
                }
            }
        }

        return $violations;
    }

    /** @return list<string> */
    private static function permissionCatalog(): array
    {
        return PermissionTableSeeder::permissionNames();
    }

    /**
     * @return array{
     *   resources: list<ResourceBinding>,
     *   measures: list<MeasureDefinition>,
     *   dimensions: list<DimensionDefinition>,
     *   joins: list<JoinDefinition>,
     *   bucket_sets: list<BucketSetDefinition>,
     *   segments: list<SegmentDefinition>,
     *   capabilities: list<CapabilityEntry>
     * }
     */
    private static function buildDefaults(): array
    {
        $resources = DefaultResources::all();
        $measures = DefaultMeasures::all();
        $dimensions = DefaultDimensions::all();
        $joins = DefaultJoins::all();
        $bucketSets = DefaultBucketSets::all();
        $segments = DefaultSegments::all();
        $capabilities = DefaultCapabilities::all($measures, $dimensions, $bucketSets, $segments);

        return [
            'resources' => $resources,
            'measures' => $measures,
            'dimensions' => $dimensions,
            'joins' => $joins,
            'bucket_sets' => $bucketSets,
            'segments' => $segments,
            'capabilities' => $capabilities,
        ];
    }
}
