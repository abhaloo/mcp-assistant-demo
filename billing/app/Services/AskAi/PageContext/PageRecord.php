<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

/**
 * @phpstan-type FieldMap array<string, string|null>
 */
final readonly class PageRecord
{
    /**
     * @param  FieldMap  $fields
     * @param  list<string>  $truncatedFields
     */
    public function __construct(
        public string $id,
        public string $label,
        public array $fields,
        public string $linkKey,
        public array $truncatedFields = [],
    ) {}

    /**
     * @return array{id: string, label: string, fields: FieldMap, link_key: string, truncated_fields: list<string>}
     */
    public function toArray(): array
    {
        return [
            'id' => $this->id,
            'label' => $this->label,
            'fields' => $this->fields,
            'link_key' => $this->linkKey,
            'truncated_fields' => array_values($this->truncatedFields),
        ];
    }
}
