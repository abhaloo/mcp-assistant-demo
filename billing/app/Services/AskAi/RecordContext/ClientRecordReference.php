<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

/**
 * One untrusted (resource_type, record_id) pair from the browser (Task B4 / R2).
 * Identifiers only — RecordContextResolver reloads the user and re-authorizes
 * every reference server-side; nothing here is trusted.
 */
final readonly class ClientRecordReference
{
    public function __construct(
        public string $resourceType,
        public string $recordId,
    ) {}
}
