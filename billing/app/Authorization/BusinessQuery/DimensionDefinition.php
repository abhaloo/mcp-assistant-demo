<?php

declare(strict_types=1);

namespace App\Authorization\BusinessQuery;

use InvalidArgumentException;

/**
 * Typed dimension over an ai_v1_bq_* projection view.
 */
readonly class DimensionDefinition
{
    /** @var list<string> */
    public const FORMATS = ['id', 'number', 'currency', 'percent', 'date', 'datetime'];

    /**
     * @param  'string'|'number'|'time'|'boolean'  $type
     * @param  list<string>  $allowedValues
     * @param  list<string>  $directRelationships
     * @param  list<string>  $exampleQuestions  card-side steering; surfaces only to callers who can see the capability
     */
    public function __construct(
        public string $name,
        public string $owningResource,
        public string $sqlExpression,
        public string $type,
        public bool $isPrimaryKey,
        public string $description,
        public array $allowedValues = [],
        public array $directRelationships = [],
        public ?string $resolvableAs = null,
        public array $exampleQuestions = [],
        public ?string $format = null,
        public ?string $label = null,
        public ?string $displayOf = null,
        public ?string $linkKey = null,
        /** Sibling member of the same resource that holds the id this look links through. */
        public ?string $linkVia = null,
    ) {
        if ($format !== null && ! in_array($format, self::FORMATS, true)) {
            throw new InvalidArgumentException("Dimension {$name}: unknown format '{$format}'.");
        }
    }

    /** @return array<string, mixed> */
    public function toExportArray(): array
    {
        return MeasureDefinition::withAdditiveKeys([
            'name' => $this->name,
            'owning_resource' => $this->owningResource,
            'sql_expression' => $this->sqlExpression,
            'type' => $this->type,
            'is_primary_key' => $this->isPrimaryKey,
            'description' => $this->description,
            'allowed_values' => $this->allowedValues,
        ], [
            'direct_relationships' => $this->directRelationships,
            'resolvable_as' => $this->resolvableAs,
            'format' => $this->format,
            'label' => $this->label,
            'display_of' => $this->displayOf,
            'link_key' => $this->linkKey,
            'link_via' => $this->linkVia,
        ]);
    }
}
