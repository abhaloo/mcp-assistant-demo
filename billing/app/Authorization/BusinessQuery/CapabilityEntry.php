<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

/**
 * Planner-visible card entry. Definitions stay private unless listed here.
 */
readonly class CapabilityEntry
{
    /**
     * @param  'measure'|'dimension'|'bucket_set'|'segment'  $kind
     * @param  list<string>  $exampleQuestions
     * @param  list<string>  $requiredFilters
     * @param  list<string>  $requiredPermissions
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     * @param  array<string, mixed>  $meta
     */
    public function __construct(
        public string $name,
        public string $kind,
        public string $resolvesTo,
        public string $title,
        public string $description,
        public array $exampleQuestions,
        public array $requiredFilters,
        public array $requiredPermissions,
        public string $capabilityState = 'enabled',
        public array $meta = [],
    ) {}
}
