<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

/**
 * One server-trusted, re-authorized record inside a `record_context` sent to RAG
 * (Task B4 / R2-R3). Every value here comes from the database via
 * BusinessRecordRegistry/PolicyManifestExporter's allowlisted fields — never from
 * the client (see RecordContextResolver).
 *
 * @phpstan-type FieldMap array<string, string>
 */
final readonly class TrustedRecordContextRecord
{
    /**
     * @param  FieldMap  $fields  Flat string->string map, already capped
     *                            (RecordContextCaps) — empty is valid and
     *                            represented on the wire as {} (see toArray()).
     */
    public function __construct(
        public string $resourceType,
        public string $recordId,
        public string $label,
        public array $fields,
        public string $linkKey,
    ) {}

    /**
     * @return array{resource_type: string, record_id: string, label: string, fields: FieldMap|\stdClass, link_key: string}
     */
    public function toArray(): array
    {
        return [
            'resource_type' => $this->resourceType,
            'record_id' => $this->recordId,
            'label' => $this->label,
            // json_encode([]) === '[]', a JSON array — RAG's fields: dict[str, str]
            // requires a JSON OBJECT. Force the empty case to '{}', matching Task
            // B3's identical fix for AiAccessPolicy::snapshot()'s `resources` field
            // and PolicyManifestExporter's `record_predicates` field. The
            // coordinator's mid-task clarification (2026-07-21) settles this: ALWAYS
            // emit {} for empty, never omit the key.
            'fields' => $this->fields === [] ? new \stdClass : $this->fields,
            'link_key' => $this->linkKey,
        ];
    }
}
