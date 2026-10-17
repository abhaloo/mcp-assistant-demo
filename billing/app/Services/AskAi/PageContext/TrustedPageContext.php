<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

/**
 * Server-trusted page context sent to RAG.
 *
 * Wire contract: version, kind, resource_type, profile, title, records only.
 * Route and scope_id terminate at Laravel and are not forwarded.
 */
final readonly class TrustedPageContext
{
    /**
     * @param  list<PageRecord>  $records
     */
    public function __construct(
        public int $version,
        public string $kind,
        public string $resourceType,
        public string $profile,
        public string $title,
        public array $records,
        public ?string $detailContract = null,
        public ?string $definitionBundleHash = null,
        public ?int $compatibilityEpoch = null,
    ) {}

    /**
     * Laravel→RAG JSON contract (canonical wire parity fixture shape).
     *
     * @return array<string, mixed>
     */
    public function toWireArray(): array
    {
        if ($this->version === 2) {
            return [
                'version' => 2,
                'profile' => $this->profile,
                'records' => array_map(
                    static fn (PageRecord $record): array => [
                        'id' => $record->id,
                        'label' => $record->label,
                        'link_key' => $record->linkKey,
                    ],
                    $this->records
                ),
                'detail_contract' => $this->detailContract ?? 'typed-detail.v2',
                'definition_bundle_hash' => $this->definitionBundleHash,
                'compatibility_epoch' => $this->compatibilityEpoch ?? 2,
            ];
        }

        return [
            'version' => $this->version,
            'kind' => $this->kind,
            'resource_type' => $this->resourceType,
            'profile' => $this->profile,
            'title' => $this->title,
            'records' => array_map(
                static fn (PageRecord $record): array => $record->toArray(),
                $this->records
            ),
        ];
    }

    /** @deprecated Use toWireArray() — retained for internal logging/tests during migration. */
    public function toArray(): array
    {
        return $this->toWireArray();
    }
}
