<?php

declare(strict_types=1);

namespace App\Services\AskAi\RecordContext;

/**
 * Server-trusted `record_context` sent to RAG (Task B4 / R2-R3, contract §
 * "Billing -> RAG (TRUSTED — after re-authorization)"). Wire shape:
 *
 *   record_context:
 *     version: 1
 *     title: "Search results for E8520"
 *     records:
 *       - resource_type: quotation
 *         record_id: "1234"
 *         label: "E8520"
 *         fields: { customer: "Acme Customer" }
 *         link_key: bills
 *
 * `toWireArray()`'s exact output is BOTH the request body's `record_context` field
 * AND the input to RecordContextDigest::compute() for the `record_context_digest`
 * JWT claim — the same value must produce both, or the digest binding RAG enforces
 * silently 401s every request (see RecordContextDigest's docblock).
 */
final readonly class TrustedRecordContext
{
    public const VERSION = 1;

    /**
     * @param  list<TrustedRecordContextRecord>  $records
     */
    public function __construct(
        public string $title,
        public array $records,
    ) {}

    /**
     * @return array{version: int, title: string, records: list<array<string, mixed>>}
     */
    public function toWireArray(): array
    {
        return [
            'version' => self::VERSION,
            'title' => $this->title,
            'records' => array_map(
                static fn (TrustedRecordContextRecord $record): array => $record->toArray(),
                $this->records,
            ),
        ];
    }
}
