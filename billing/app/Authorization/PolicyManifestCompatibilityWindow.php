<?php

declare(strict_types=1);

namespace App\Authorization;

use JsonException;
use RuntimeException;

/**
 * Reads Billing's content-addressed policy-manifest current/N-1 window.
 *
 * The index is a producer-owned compatibility contract. It is deliberately
 * validated together with every referenced immutable bundle so a stale,
 * missing, or tampered pointer cannot widen or silently narrow the policy
 * hashes emitted in a business-definition bundle.
 */
final class PolicyManifestCompatibilityWindow
{
    private const int INDEX_SCHEMA_VERSION = 1;

    private const int MAX_ACCEPTED_HASHES = 2;

    private const string HASH_PATTERN = '/^sha256:[0-9a-f]{64}$/';

    public function __construct(
        private readonly string $indexPath = '',
        private readonly string $bundlesDirectory = '',
    ) {}

    /**
     * @return list<string> Current hash first, followed by at most one N-1 hash.
     */
    public function hashes(): array
    {
        $indexPath = $this->indexPath !== ''
            ? $this->indexPath
            : base_path('database/ai-manifest/index.json');
        $bundlesDirectory = $this->bundlesDirectory !== ''
            ? $this->bundlesDirectory
            : base_path('database/ai-manifest/bundles');

        if (! is_file($indexPath)) {
            throw new RuntimeException("Policy manifest compatibility index is missing: {$indexPath}");
        }

        $bytes = file_get_contents($indexPath);
        if ($bytes === false || $bytes === '') {
            throw new RuntimeException("Policy manifest compatibility index is unreadable: {$indexPath}");
        }

        try {
            $index = json_decode($bytes, true, 512, JSON_THROW_ON_ERROR);
        } catch (JsonException $exception) {
            throw new RuntimeException(
                "Policy manifest compatibility index is invalid JSON: {$indexPath}",
                previous: $exception,
            );
        }

        if (! is_array($index)
            || ($index['schema_version'] ?? null) !== self::INDEX_SCHEMA_VERSION
            || ! is_string($index['current'] ?? null)
            || ! is_array($index['accepted'] ?? null)
        ) {
            throw new RuntimeException(
                "Policy manifest compatibility index has an invalid shape: {$indexPath}",
            );
        }

        $current = $index['current'];
        $accepted = array_values($index['accepted']);
        if (! $this->isHash($current)
            || count($accepted) < 1
            || count($accepted) > self::MAX_ACCEPTED_HASHES
            || $accepted[0] !== $current
            || count(array_unique($accepted)) !== count($accepted)
            || array_filter($accepted, fn (mixed $hash): bool => ! is_string($hash) || ! $this->isHash($hash)) !== []
        ) {
            throw new RuntimeException(
                "Policy manifest compatibility index must contain current then at most one valid previous hash: {$indexPath}",
            );
        }

        foreach ($accepted as $hash) {
            $bundlePath = rtrim($bundlesDirectory, '/\\').DIRECTORY_SEPARATOR.substr($hash, strlen('sha256:')).'.json';
            if (! is_file($bundlePath)) {
                throw new RuntimeException("Policy manifest bundle for {$hash} is missing: {$bundlePath}");
            }

            $bundleBytes = file_get_contents($bundlePath);
            if ($bundleBytes === false || hash('sha256', $bundleBytes) !== substr($hash, strlen('sha256:'))) {
                throw new RuntimeException("Policy manifest bundle for {$hash} failed integrity validation: {$bundlePath}");
            }

            try {
                json_decode($bundleBytes, true, 512, JSON_THROW_ON_ERROR);
            } catch (JsonException $exception) {
                throw new RuntimeException(
                    "Policy manifest bundle for {$hash} is invalid JSON: {$bundlePath}",
                    previous: $exception,
                );
            }
        }

        return $accepted;
    }

    private function isHash(string $hash): bool
    {
        return preg_match(self::HASH_PATTERN, $hash) === 1;
    }
}
