<?php

declare(strict_types=1);

namespace App\Console\Commands;

use App\Authorization\BusinessQuery\BusinessDefinitionsExporter;
use Illuminate\Console\Command;
use RuntimeException;

/**
 * Export the content-addressed business-definitions bundle (Billing producer half).
 *
 * Writes:
 *  1. Mutable current artifact: business-definitions.json + .hash
 *  2. Immutable hash-addressed bundle: bundles/<hex>.json
 *  3. index.json current/accepted pointer (≤2) for RAG N/N-1 staging
 */
final class ExportAiBusinessDefinitions extends Command
{
    protected $signature = 'ai-business-definitions:export
                            {--stage-rag= : Optional absolute path to RAG app/business_query/bundle directory}';

    protected $description = 'Export the content-addressed AI business-definitions bundle';

    private const string ARTIFACT_RELATIVE_PATH = 'database/ai-business-definitions/business-definitions.json';

    private const string HASH_RELATIVE_PATH = 'database/ai-business-definitions/business-definitions.hash';

    private const string INDEX_RELATIVE_PATH = 'database/ai-business-definitions/index.json';

    private const string BUNDLES_RELATIVE_DIR = 'database/ai-business-definitions/bundles';

    public function __construct(
        private readonly BusinessDefinitionsExporter $exporter = new BusinessDefinitionsExporter,
    ) {
        parent::__construct();
    }

    public function handle(): int
    {
        $canonicalJson = $this->exporter->canonicalJsonWithHash();
        $hash = $this->exporter->hash();
        $hex = substr($hash, strlen('sha256:'));

        try {
            return $this->withExclusiveLock(storage_path('framework/ai-business-definitions.export.lock'), function () use ($canonicalJson, $hash, $hex): int {
                $published = $this->publishLocalArtifacts($canonicalJson, $hash, $hex);
                if ($published !== self::SUCCESS) {
                    return $published;
                }

                $stageRag = $this->option('stage-rag');
                if (is_string($stageRag) && $stageRag !== '') {
                    $index = $this->readPublishedIndex();
                    $index = $this->rotateConsumerIndex($stageRag, $hash, $index);

                    return $this->stageToRag($stageRag, $hash, $hex, $canonicalJson, $index);
                }

                return self::SUCCESS;
            });
        } catch (RuntimeException $e) {
            $this->components->error($e->getMessage());

            return self::FAILURE;
        }
    }

    private function publishLocalArtifacts(string $canonicalJson, string $hash, string $hex): int
    {
        $bundlesDir = base_path(self::BUNDLES_RELATIVE_DIR);
        $bundlePath = $bundlesDir.DIRECTORY_SEPARATOR.$hex.'.json';

        if (is_file($bundlePath)) {
            $existing = file_get_contents($bundlePath);
            if ($existing === false) {
                $this->components->error("Could not read existing bundle [bundles/{$hex}.json] — check file permissions.");

                return self::FAILURE;
            }
            if ($existing !== $canonicalJson) {
                $this->components->error(
                    "Refusing to overwrite existing bundle [bundles/{$hex}.json] with different bytes for ".
                    'the same hash. Investigate before re-running.',
                );

                return self::FAILURE;
            }
            $this->components->twoColumnDetail("bundles/{$hex}.json", '<fg=green>already up to date</>');
        } else {
            if (! is_dir($bundlesDir) && ! mkdir($bundlesDir, 0755, true) && ! is_dir($bundlesDir)) {
                $this->components->error("Could not create bundles directory [{$bundlesDir}].");

                return self::FAILURE;
            }
            if (! $this->publishFile($bundlePath, $canonicalJson)) {
                $this->components->error("Could not write bundle [bundles/{$hex}.json].");

                return self::FAILURE;
            }
            $this->components->twoColumnDetail("bundles/{$hex}.json", '<fg=green>written</>');
        }

        $indexPath = base_path(self::INDEX_RELATIVE_PATH);
        $index = $this->buildIndex($hash, $indexPath);
        $indexJson = json_encode(
            $index,
            JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT,
        )."\n";

        if (! $this->publishFile(base_path(self::ARTIFACT_RELATIVE_PATH), $canonicalJson)) {
            $this->components->error('Could not write '.self::ARTIFACT_RELATIVE_PATH.'.');

            return self::FAILURE;
        }
        if (! $this->publishFile(base_path(self::HASH_RELATIVE_PATH), $hash)) {
            $this->components->error('Could not write '.self::HASH_RELATIVE_PATH.'.');

            return self::FAILURE;
        }
        if (! $this->publishFile($indexPath, $indexJson)) {
            $this->components->error('Could not write '.self::INDEX_RELATIVE_PATH.'.');

            return self::FAILURE;
        }

        $this->components->twoColumnDetail(self::ARTIFACT_RELATIVE_PATH, '<fg=green>written</>');
        $this->components->twoColumnDetail(self::HASH_RELATIVE_PATH, '<fg=green>written</>');
        $this->components->twoColumnDetail(self::INDEX_RELATIVE_PATH, '<fg=green>written</>');
        $this->components->info("Exported business-definitions hash: {$hash}");

        return self::SUCCESS;
    }

    /**
     * @return array{schema_version: int, current: string, accepted: list<string>}
     */
    private function readPublishedIndex(): array
    {
        $indexPath = base_path(self::INDEX_RELATIVE_PATH);
        $raw = json_decode((string) file_get_contents($indexPath), true, 512, JSON_THROW_ON_ERROR);
        if (! is_array($raw) || ! isset($raw['current'], $raw['accepted']) || ! is_array($raw['accepted'])) {
            throw new RuntimeException('Published index.json is not a complete pointer set.');
        }

        return [
            'schema_version' => (int) ($raw['schema_version'] ?? 1),
            'current' => (string) $raw['current'],
            'accepted' => array_values(array_filter(
                $raw['accepted'],
                static fn (mixed $hash): bool => is_string($hash),
            )),
        ];
    }

    /**
     * @return array{schema_version: int, current: string, accepted: list<string>}
     */
    private function buildIndex(string $hash, string $indexPath): array
    {
        $accepted = [$hash];
        if (is_file($indexPath)) {
            $raw = json_decode((string) file_get_contents($indexPath), true);
            if (is_array($raw) && isset($raw['accepted']) && is_array($raw['accepted'])) {
                foreach ($raw['accepted'] as $prior) {
                    if (is_string($prior) && $prior !== $hash && $this->hasCompatibilityEpoch($prior, $this->exporter->compatibilityEpoch())) {
                        $accepted[] = $prior;
                    }
                }
            }
        }

        $accepted = array_values(array_slice($accepted, 0, 2));

        return [
            'schema_version' => 1,
            'current' => $hash,
            'accepted' => $accepted,
        ];
    }

    private function hasCompatibilityEpoch(string $hash, int $epoch): bool
    {
        $hex = substr($hash, strlen('sha256:'));
        $path = base_path(self::BUNDLES_RELATIVE_DIR.DIRECTORY_SEPARATOR.$hex.'.json');
        if (! is_file($path)) {
            return false;
        }
        $bytes = file_get_contents($path);
        if ($bytes === false || $bytes === '') {
            return false;
        }
        $bundle = json_decode($bytes, true);
        if (! is_array($bundle)) {
            return false;
        }
        if (($bundle['content_hash'] ?? null) !== $hash) {
            return false;
        }

        return ($bundle['compatibility_epoch'] ?? null) === $epoch;
    }

    /**
     * Rotate the consumer's current bundle to accepted-previous before
     * publishing the new producer bundle. The consumer may have a different
     * previous hash than Billing (for example while it is one release behind);
     * retaining the consumer's current hash keeps mixed-version rollback
     * possible and avoids replacing its accepted set with a producer-local
     * pointer that the consumer has never loaded.
     *
     * @param  array{schema_version: int, current: string, accepted: list<string>}  $producerIndex
     * @return array{schema_version: int, current: string, accepted: list<string>}
     */
    private function rotateConsumerIndex(string $ragBundleDir, string $hash, array $producerIndex): array
    {
        $targetIndexPath = rtrim($ragBundleDir, '/\\').DIRECTORY_SEPARATOR.'index.json';
        $targetBundlesDir = rtrim($ragBundleDir, '/\\').DIRECTORY_SEPARATOR.'bundles';
        $accepted = [$hash];

        if (is_file($targetIndexPath)) {
            $raw = json_decode((string) file_get_contents($targetIndexPath), true);
            $prior = is_array($raw) ? ($raw['current'] ?? null) : null;
            if (is_string($prior) && $prior !== $hash && preg_match('/^sha256:[0-9a-fA-F]{64}$/', $prior) === 1) {
                $priorPath = $targetBundlesDir.DIRECTORY_SEPARATOR.substr($prior, strlen('sha256:')).'.json';
                if (is_file($priorPath)) {
                    $bytes = file_get_contents($priorPath);
                    $decoded = is_string($bytes) ? json_decode($bytes, true) : null;
                    if (is_array($decoded) && ($decoded['content_hash'] ?? null) === $prior) {
                        $accepted[] = $prior;
                    }
                }
            }
        }

        // A fresh consumer staging directory has no current pointer yet. In
        // that case the producer's accepted previous remains the safest
        // compatibility candidate, provided its bytes are staged locally.
        if (count($accepted) === 1) {
            foreach ($producerIndex['accepted'] as $prior) {
                if ($prior === $hash || ! preg_match('/^sha256:[0-9a-fA-F]{64}$/', $prior)) {
                    continue;
                }
                $priorPath = base_path(self::BUNDLES_RELATIVE_DIR.DIRECTORY_SEPARATOR.substr($prior, strlen('sha256:')).'.json');
                if (is_file($priorPath)) {
                    $accepted[] = $prior;
                    break;
                }
            }
        }

        return [
            'schema_version' => 1,
            'current' => $hash,
            'accepted' => array_values(array_slice($accepted, 0, 2)),
        ];
    }

    /**
     * @param  array{schema_version: int, current: string, accepted: list<string>}  $index
     */
    private function stageToRag(string $ragBundleDir, string $hash, string $hex, string $canonicalJson, array $index): int
    {
        $lockPath = rtrim($ragBundleDir, '/\\').DIRECTORY_SEPARATOR.'.export.lock';

        return $this->withExclusiveLock($lockPath, function () use ($ragBundleDir, $hash, $hex, $canonicalJson, $index): int {
            $bundlesDir = rtrim($ragBundleDir, '/\\').DIRECTORY_SEPARATOR.'bundles';
            if (! is_dir($bundlesDir) && ! mkdir($bundlesDir, 0755, true) && ! is_dir($bundlesDir)) {
                $this->components->error("Could not create RAG staging bundles dir [{$bundlesDir}].");

                return self::FAILURE;
            }

            $bundlePath = $bundlesDir.DIRECTORY_SEPARATOR.$hex.'.json';
            if (is_file($bundlePath)) {
                $existing = file_get_contents($bundlePath);
                if ($existing === false) {
                    $this->components->error("Could not read staged RAG bundle [{$bundlePath}].");

                    return self::FAILURE;
                }
                if ($existing !== $canonicalJson) {
                    $this->components->error(
                        "Refusing to overwrite existing RAG bundle [{$bundlePath}] with different bytes for ".
                        'the same hash. Investigate before re-running.',
                    );

                    return self::FAILURE;
                }
            } elseif (! $this->publishFile($bundlePath, $canonicalJson)) {
                $this->components->error("Could not stage RAG bundle [{$bundlePath}].");

                return self::FAILURE;
            }

            $indexJson = json_encode(
                $index,
                JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT,
            )."\n";
            $indexPath = rtrim($ragBundleDir, '/\\').DIRECTORY_SEPARATOR.'index.json';
            if (! $this->publishFile($indexPath, $indexJson)) {
                $this->components->error("Could not stage RAG index [{$indexPath}].");

                return self::FAILURE;
            }

            $this->components->info("Staged bundle for RAG at {$ragBundleDir} ({$hash})");

            return self::SUCCESS;
        });
    }

    private function publishFile(string $path, string $contents): bool
    {
        $dir = dirname($path);
        if (! is_dir($dir) && ! mkdir($dir, 0755, true) && ! is_dir($dir)) {
            return false;
        }
        $tmp = $dir.DIRECTORY_SEPARATOR.'.'.basename($path).'.'.bin2hex(random_bytes(8)).'.tmp';
        if (file_put_contents($tmp, $contents, LOCK_EX) === false) {
            @unlink($tmp);

            return false;
        }
        if (is_file($path) && PHP_OS_FAMILY === 'Windows') {
            @unlink($path);
        }
        if (! rename($tmp, $path)) {
            @unlink($tmp);

            return false;
        }

        return true;
    }

    /**
     * @template T
     *
     * @param  callable(): T  $callback
     * @return T
     */
    private function withExclusiveLock(string $lockPath, callable $callback): mixed
    {
        $dir = dirname($lockPath);
        if (! is_dir($dir) && ! mkdir($dir, 0755, true) && ! is_dir($dir)) {
            throw new RuntimeException("Could not create lock directory [{$dir}].");
        }
        $handle = fopen($lockPath, 'c');
        if ($handle === false) {
            throw new RuntimeException("Could not open lock [{$lockPath}].");
        }
        try {
            if (! flock($handle, LOCK_EX)) {
                throw new RuntimeException("Could not acquire exclusive lock [{$lockPath}].");
            }

            return $callback();
        } finally {
            flock($handle, LOCK_UN);
            fclose($handle);
        }
    }
}
