<?php

declare(strict_types=1);

namespace App\Services\AskAi\PageContext;

/**
 * Untrusted browser payload — IDs only (+ optional scope_id parent selector).
 * Laravel rebuilds fields and re-authorizes every record.
 *
 * @phpstan-type ClientPayload array{
 *     version: int,
 *     context_id: string,
 *     route: string,
 *     kind: string,
 *     resource_type: string,
 *     record_ids: list<string>,
 *     scope_id?: string
 * }
 */
final readonly class ClientPageContext
{
    private const ALLOWED_KEYS_V1 = [
        'version',
        'context_id',
        'route',
        'kind',
        'resource_type',
        'record_ids',
        'scope_id',
    ];

    private const ALLOWED_KEYS_V2 = [
        'version',
        'profile',
        'records',
        'detail_contract',
        'definition_bundle_hash',
        'compatibility_epoch',
        'context_id',
    ];

    /**
     * @param  list<string>  $recordIds
     */
    public function __construct(
        public int $version,
        public string $contextId,
        public string $route,
        public string $kind,
        public string $resourceType,
        public array $recordIds,
        public ?string $scopeId = null,
        public ?string $profile = null,
        public ?string $detailContract = null,
        public ?string $definitionBundleHash = null,
        public ?int $compatibilityEpoch = null,
    ) {}

    /**
     * @param  array<string, mixed>  $payload
     *
     * @throws InvalidPageContextException
     */
    public static function fromArray(array $payload): self
    {
        if (! array_key_exists('version', $payload)) {
            throw new InvalidPageContextException('Missing page_context key: version', 'missing_keys');
        }

        if (! is_int($payload['version']) && ! (is_string($payload['version']) && ctype_digit($payload['version']))) {
            throw new InvalidPageContextException('Invalid page_context version.', 'invalid_version');
        }

        $version = (int) $payload['version'];

        if ($version === 2) {
            return self::fromV2Array($payload);
        }

        return self::fromV1Array($payload, $version);
    }

    /**
     * @param  array<string, mixed>  $payload
     *
     * @throws InvalidPageContextException
     */
    private static function fromV1Array(array $payload, int $version): self
    {
        $unknown = array_diff(array_keys($payload), self::ALLOWED_KEYS_V1);
        if ($unknown !== []) {
            throw new InvalidPageContextException('Unknown page_context keys.', 'unknown_keys');
        }

        foreach (['version', 'context_id', 'route', 'kind', 'resource_type', 'record_ids'] as $key) {
            if (! array_key_exists($key, $payload)) {
                throw new InvalidPageContextException('Missing page_context key: '.$key, 'missing_keys');
            }
        }

        $contextId = is_string($payload['context_id']) ? trim($payload['context_id']) : '';
        $route = is_string($payload['route']) ? trim($payload['route']) : '';
        $kind = is_string($payload['kind']) ? trim($payload['kind']) : '';
        $resourceType = is_string($payload['resource_type']) ? trim($payload['resource_type']) : '';

        if ($contextId === '' || $route === '' || $kind === '' || $resourceType === '') {
            throw new InvalidPageContextException('Invalid page_context identity fields.', 'invalid_identity_fields');
        }

        if (! is_array($payload['record_ids'])) {
            throw new InvalidPageContextException('Invalid page_context record_ids.', 'invalid_record_ids');
        }

        $recordIds = self::normalizeRecordIds($payload['record_ids']);

        $profile = JobsContextProfile::find($version, $route, $kind, $resourceType);
        if ($profile === null) {
            throw new InvalidPageContextException('Unsupported page_context route tuple.', 'unsupported_route_tuple');
        }

        if ($profile['exact_count'] !== null && count($recordIds) !== $profile['exact_count']) {
            throw new InvalidPageContextException('Invalid page_context record count.', 'invalid_record_count');
        }

        if (count($recordIds) > $profile['max_records']) {
            throw new InvalidPageContextException('Too many page_context record ids.', 'too_many_records');
        }

        $scopeId = null;
        if (array_key_exists('scope_id', $payload)) {
            $scopeId = self::normalizeScopeId($payload['scope_id']);
        }

        if ($profile['requires_scope_id'] && $scopeId === null) {
            throw new InvalidPageContextException('Missing page_context scope_id.', 'missing_scope_id');
        }

        if (! $profile['requires_scope_id'] && $scopeId !== null) {
            throw new InvalidPageContextException('Unexpected page_context scope_id.', 'unexpected_scope_id');
        }

        return new self(
            version: $version,
            contextId: $contextId,
            route: $route,
            kind: $kind,
            resourceType: $resourceType,
            recordIds: $recordIds,
            scopeId: $scopeId,
            profile: $profile['profile'],
        );
    }

    /**
     * @param  array<string, mixed>  $payload
     *
     * @throws InvalidPageContextException
     */
    private static function fromV2Array(array $payload): self
    {
        $unknown = array_diff(array_keys($payload), self::ALLOWED_KEYS_V2);
        if ($unknown !== []) {
            throw new InvalidPageContextException('Unknown page_context keys.', 'unknown_keys');
        }

        foreach (['version', 'profile', 'records', 'detail_contract', 'definition_bundle_hash', 'compatibility_epoch'] as $key) {
            if (! array_key_exists($key, $payload)) {
                throw new InvalidPageContextException('Missing page_context key: '.$key, 'missing_keys');
            }
        }

        $profileName = is_string($payload['profile']) ? trim($payload['profile']) : '';
        $detailContract = is_string($payload['detail_contract']) ? trim($payload['detail_contract']) : '';
        $bundleHash = is_string($payload['definition_bundle_hash']) ? trim($payload['definition_bundle_hash']) : '';
        $epoch = is_int($payload['compatibility_epoch']) ? $payload['compatibility_epoch'] : null;

        if ($profileName === '' || $detailContract === '' || $bundleHash === '' || $epoch === null) {
            throw new InvalidPageContextException('Invalid page_context identity fields.', 'invalid_identity_fields');
        }

        if (! str_starts_with($bundleHash, 'sha256:')) {
            throw new InvalidPageContextException('Invalid definition_bundle_hash format.', 'invalid_bundle_hash');
        }

        if (! is_array($payload['records'])) {
            throw new InvalidPageContextException('Invalid page_context records.', 'invalid_records');
        }

        $rawRecordIds = [];
        foreach ($payload['records'] as $recordItem) {
            if (! is_array($recordItem) || ! isset($recordItem['id'])) {
                throw new InvalidPageContextException('Invalid page_context record shape.', 'invalid_records');
            }
            $rawRecordIds[] = $recordItem['id'];
        }

        $recordIds = self::normalizeRecordIds($rawRecordIds);

        $profile = JobsContextProfile::findByProfile(2, $profileName);
        if ($profile === null) {
            throw new InvalidPageContextException('Unsupported page_context profile.', 'unsupported_profile');
        }

        if ($profile['exact_count'] !== null && count($recordIds) !== $profile['exact_count']) {
            throw new InvalidPageContextException('Invalid page_context record count.', 'invalid_record_count');
        }

        $contextId = isset($payload['context_id']) && is_string($payload['context_id'])
            ? trim($payload['context_id'])
            : 'jobs:'.$profileName.':'.implode(',', $recordIds);

        return new self(
            version: 2,
            contextId: $contextId,
            route: $profile['route'],
            kind: $profile['kind'],
            resourceType: 'work_order',
            recordIds: $recordIds,
            scopeId: null,
            profile: $profileName,
            detailContract: $detailContract,
            definitionBundleHash: $bundleHash,
            compatibilityEpoch: $epoch,
        );
    }

    /**
     * @param  list<mixed>  $rawIds
     * @return list<string>
     */
    private static function normalizeRecordIds(array $rawIds): array
    {
        $recordIds = [];
        $seen = [];
        foreach ($rawIds as $id) {
            if (! is_string($id) && ! is_int($id)) {
                throw new InvalidPageContextException('Invalid page_context record id.', 'invalid_record_id');
            }
            $normalized = is_int($id) ? (string) $id : trim($id);
            if ($normalized === '' || ! ctype_digit($normalized) || (int) $normalized < 1) {
                throw new InvalidPageContextException('Invalid page_context record id.', 'invalid_record_id');
            }
            $normalized = (string) (int) $normalized;
            if (isset($seen[$normalized])) {
                throw new InvalidPageContextException('Duplicate page_context record ids.', 'duplicate_record_ids');
            }
            $seen[$normalized] = true;
            $recordIds[] = $normalized;
        }

        return $recordIds;
    }

    /**
     * @throws InvalidPageContextException
     */
    private static function normalizeScopeId(mixed $raw): string
    {
        if (! is_string($raw) && ! is_int($raw)) {
            throw new InvalidPageContextException('Invalid page_context scope_id.', 'invalid_scope_id');
        }
        $normalized = is_int($raw) ? (string) $raw : trim($raw);
        if ($normalized === '' || ! ctype_digit($normalized) || (int) $normalized < 1) {
            throw new InvalidPageContextException('Invalid page_context scope_id.', 'invalid_scope_id');
        }

        return (string) (int) $normalized;
    }
}
