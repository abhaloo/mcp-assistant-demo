<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery\Catalog;

use App\Authorization\BusinessQuery\BucketSetDefinition;
use App\Authorization\BusinessQuery\CapabilityEntry;
use App\Authorization\BusinessQuery\DimensionDefinition;
use App\Authorization\BusinessQuery\MeasureDefinition;
use App\Authorization\BusinessQuery\SegmentDefinition;
use InvalidArgumentException;

final class DefaultCapabilities
{
    /**
     * @param  list<MeasureDefinition>  $measures
     * @param  list<DimensionDefinition>  $dimensions
     * @param  list<BucketSetDefinition>  $bucketSets
     * @param  list<SegmentDefinition>  $segments
     * @return list<CapabilityEntry>
     */
    public static function all(array $measures, array $dimensions, array $bucketSets, array $segments): array
    {
        $permissionFor = static function (string $resource): array {
            return match ($resource) {
                'invoice', 'invoice_item' => ['view invoice'],
                'quotation', 'quotation_item' => ['view quotation'],
                'payable_quotation', 'payable_quotation_item' => ['view payable quotation'],
                'customer_order' => ['view customer order'],
                'job', 'work_order_item' => ['view job'],
                'customer' => ['view customer'],
                'supplier' => ['view supplier'],
                'inventory' => ['view inventory'],
                'ledger_entry' => ['view journal entries'],
                // Fail closed. An unmapped resource used to fall through to [],
                // which publishes every one of its capabilities to any caller
                // holding no permission at all -- the failure is invisible
                // because the capability card simply shows more than it should.
                default => throw new InvalidArgumentException(
                    "No permission mapping declared for resource [{$resource}]."
                ),
            };
        };

        $entries = [];

        foreach ($measures as $measure) {
            $pending = $measure->capabilityState === 'decision_pending';
            $entries[] = new CapabilityEntry(
                name: $measure->name,
                kind: 'measure',
                resolvesTo: $measure->name,
                title: str_replace('_', ' ', ucfirst($measure->name)),
                description: $measure->description,
                exampleQuestions: $measure->exampleQuestions,
                requiredFilters: [],
                requiredPermissions: $pending ? [] : $permissionFor($measure->owningResource),
                capabilityState: $measure->capabilityState,
                meta: in_array($measure->name, CatalogConstants::AR_AGING_RESULT_SHAPE, true)
                    ? ['ar_aging_result_shape' => true]
                    : [],
            );
        }

        foreach ($dimensions as $dimension) {
            $entries[] = new CapabilityEntry(
                name: $dimension->name,
                kind: 'dimension',
                resolvesTo: $dimension->name,
                title: $dimension->name,
                description: $dimension->description,
                exampleQuestions: $dimension->exampleQuestions,
                requiredFilters: [],
                requiredPermissions: $permissionFor($dimension->owningResource),
                capabilityState: 'enabled',
            );
        }

        foreach ($bucketSets as $set) {
            $entries[] = new CapabilityEntry(
                name: $set->name,
                kind: 'bucket_set',
                resolvesTo: $set->name,
                title: str_replace('_', ' ', ucfirst($set->name)),
                description: $set->description,
                exampleQuestions: $set->exampleQuestions,
                requiredFilters: [],
                requiredPermissions: $permissionFor($set->owningResource),
                capabilityState: 'enabled',
                meta: ['result_shape' => $set->resultShape],
            );
        }

        foreach ($segments as $segment) {
            $entries[] = new CapabilityEntry(
                name: $segment->name,
                kind: 'segment',
                resolvesTo: $segment->name,
                title: $segment->displayTitle(),
                description: $segment->description,
                exampleQuestions: [],
                requiredFilters: [],
                requiredPermissions: $permissionFor($segment->owningResource),
                capabilityState: $segment->capabilityState,
            );
        }

        return $entries;
    }
}
