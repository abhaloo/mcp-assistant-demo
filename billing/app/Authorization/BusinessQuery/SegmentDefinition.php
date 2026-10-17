<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

/**
 * Filter-only segment over an ai_v1_bq_* projection view.
 */
readonly class SegmentDefinition
{
    /**
     * @param  'enabled'|'decision_pending'|'unsupported'  $capabilityState
     */
    public function __construct(
        public string $name,
        public string $owningResource,
        public string $predicateSql,
        public string $description,
        public string $capabilityState = 'enabled',
        public ?string $title = null,
    ) {}

    public function displayTitle(): string
    {
        return $this->title ?? $this->name;
    }

    /** @return array<string, mixed> */
    public function toExportArray(): array
    {
        return [
            'name' => $this->name,
            'owning_resource' => $this->owningResource,
            'predicate_sql' => $this->predicateSql,
            'description' => $this->description,
            'capability_state' => $this->capabilityState,
        ];
    }
}
